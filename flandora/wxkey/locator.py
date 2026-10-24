"""从磁盘上的 Weixin.dll 结构化定位「数据库口令设置函数」。

不维护版本表：微信把数据库口令交给 WCDB 时，调用形态是
    mov r8d, 0x1000      ; 页大小
    mov r9d, <兼容级别>  ; 当前版本取 3 或 4
    call <口令设置函数>
这段机器码在 4.1.15.56 里出现 8 次，全部指向同一个函数。于是定位规则是：
扫 .text 找这段调用形态，把每个调用点的目标地址按 .pdata 的函数边界归并，
若最终只剩一个函数，它就是口令设置函数。

这条规则依赖的是「传页大小与兼容级别去调用」这一调用约定，而不是任何固定偏移；
若微信改了参数顺序或页大小，扫描结果会不唯一或不命中，此时抛 UnsupportedBuild，
由上层提示「当前微信版本尚未支持」，绝不猜一个地址去挂钩。

定位结果只是静态事实；真正的把关在 recover 阶段——取到的材料必须能通过
SQLCipher 页 1 的 HMAC 校验才算数。
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import struct
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("wxkey.locator")

# PE 里 .pdata 的 RuntimeFunction 数组下标（IMAGE_DIRECTORY_ENTRY_EXCEPTION）
DIR_EXCEPTION = 3

# 调用形态特征：mov r8d,0x1000 ; mov r9d,imm32 ; call rel32
SIG_MOV_R8D = b"\x41\xb8\x00\x10\x00\x00\x41\xb9"
SIG_CALL = 0xE8
SIG_LEN = 17  # 上面那段特征到 call 之后的下一条指令

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MAX_PATH = 260
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class LocatorError(Exception):
    """定位失败。"""


class UnsupportedBuild(LocatorError):
    """当前微信版本的结构不符合已知调用形态，无法定位。"""


@dataclass(frozen=True)
class HookPoint:
    """一个可挂钩的位置。"""

    rva: int
    size: int
    dll_path: str
    dll_sha256: str
    file_version: str

    @property
    def va(self) -> int:
        """按 PE 首选基址算出的映像内地址；运行期还要再加上实际加载基址差。"""
        return IMAGE_MIN + self.rva

    def describe(self) -> str:
        ver = self.file_version or "未知版本"
        return (
            f"微信 {ver}，口令设置函数 RVA {self.rva:#x}"
            f"（{self.size} 字节），Weixin.dll {self.dll_sha256[:16]}…"
        )


# PE 可选头里的首选基址在 4.1.15.56 上是 0x180000000；真正挂钩时用运行期基址。
IMAGE_MIN = 0x180000000


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


class PeImage:
    """只解析定位需要的那部分 PE 结构：段表与 .pdata 的函数边界。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        try:
            self.data = self.path.read_bytes()
        except OSError as exc:
            raise LocatorError(f"读不了 {self.path}：{exc}") from exc
        self._parse_headers()
        self._parse_functions()

    def _parse_headers(self) -> None:
        data = self.data
        if data[:2] != b"MZ":
            raise LocatorError(f"{self.path} 不是 PE 文件")
        lfanew = _u32(data, 0x3C)
        if data[lfanew : lfanew + 4] != b"PE\0\0":
            raise LocatorError(f"{self.path} 的 PE 签名不对")
        n_sec = _u16(data, lfanew + 6)
        opt_size = _u16(data, lfanew + 20)
        opt = lfanew + 24
        magic = _u16(data, opt)
        if magic == 0x20B:
            dd_off = opt + 112
            preferred = struct.unpack_from("<Q", data, opt + 24)[0]
        elif magic == 0x10B:
            dd_off = opt + 96
            preferred = _u32(data, opt + 28)
        else:
            raise LocatorError(f"未知的可选头 magic {magic:#x}")
        global IMAGE_MIN  # noqa: PLW0603  —— 只有解析首部这一处会改它
        IMAGE_MIN = preferred

        self.sections: dict[str, tuple[int, int, int, int]] = {}
        sec_off = opt + opt_size
        for i in range(n_sec):
            o = sec_off + i * 40
            name = data[o : o + 8].rstrip(b"\0").decode("latin1")
            vsize = _u32(data, o + 8)
            vaddr = _u32(data, o + 12)
            rawsize = _u32(data, o + 16)
            rawptr = _u32(data, o + 20)
            self.sections[name] = (vaddr, vsize, rawptr, rawsize)

        exc_rva = _u32(data, dd_off + DIR_EXCEPTION * 8)
        exc_size = _u32(data, dd_off + DIR_EXCEPTION * 8 + 4)
        self._exc = (exc_rva, exc_size)

    def section_of(self, rva: int) -> Optional[str]:
        for name, (va, vsize, _rp, _rs) in self.sections.items():
            if va <= rva < va + max(vsize, 1):
                return name
        return None

    def _parse_functions(self) -> None:
        exc_rva, exc_size = self._exc
        if not exc_rva or not exc_size:
            raise UnsupportedBuild("Weixin.dll 没有异常目录（.pdata），无法取得函数边界")
        raw = self._rva_to_file(exc_rva)
        if raw is None:
            raise UnsupportedBuild(".pdata 的地址换不到文件偏移")
        funcs = []
        for i in range(exc_size // 12):
            begin, end, _unwind = struct.unpack_from("<III", self.data, raw + i * 12)
            if begin and end > begin:
                funcs.append((begin, end))
        if not funcs:
            raise UnsupportedBuild(".pdata 里没有有效的函数条目")
        funcs.sort()
        self.funcs = funcs
        self._starts = [b for b, _ in funcs]

    def _rva_to_file(self, rva: int) -> Optional[int]:
        for _name, (va, vsize, rawptr, rawsize) in self.sections.items():
            if va <= rva < va + max(vsize, rawsize):
                return rawptr + (rva - va)
        return None

    def func_of(self, rva: int) -> Optional[tuple[int, int]]:
        lo, hi = 0, len(self.funcs) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            begin, end = self.funcs[mid]
            if rva < begin:
                hi = mid - 1
            elif rva >= end:
                lo = mid + 1
            else:
                return (begin, end)
        return None

    def text(self) -> tuple[int, bytes]:
        if ".text" not in self.sections:
            raise UnsupportedBuild("Weixin.dll 没有 .text 段")
        va, _vsize, rawptr, rawsize = self.sections[".text"]
        return va, self.data[rawptr : rawptr + rawsize]


def _file_version(path: Path) -> str:
    """读 PE 的版本资源；读不到就返回空串。"""
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf):
        return ""
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return ""
    if not length.value:
        return ""

    class FixedFileInfo(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD),
            ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD),
            ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD),
            ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD),
            ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    info = ctypes.cast(ptr, ctypes.POINTER(FixedFileInfo)).contents
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def scan_key_setter(image: PeImage) -> tuple[int, list[int]]:
    """按调用形态扫 .text，返回 (目标函数起始 RVA, 所有调用点 RVA)。"""
    text_va, text = image.text()
    targets: dict[int, list[int]] = {}
    sites: list[int] = []
    pos = 0
    while True:
        i = text.find(SIG_MOV_R8D, pos)
        if i < 0:
            break
        pos = i + 1
        if i + SIG_LEN > len(text):
            break
        if text[i + 12] != SIG_CALL:
            continue
        disp = struct.unpack_from("<i", text, i + 13)[0]
        site = text_va + i
        callee = site + SIG_LEN + disp
        fn = image.func_of(callee)
        if fn is None:
            continue
        sites.append(site)
        targets.setdefault(fn[0], []).append(site)

    if not targets:
        raise UnsupportedBuild(
            "在 Weixin.dll 里没有找到「传页大小与兼容级别」的调用形态，当前微信版本可能改了实现"
        )
    if len(targets) > 1:
        detail = "，".join(
            f"{rva:#x}（{len(s)} 处调用）"
            for rva, s in sorted(targets.items(), key=lambda x: -len(x[1]))
        )
        raise UnsupportedBuild(f"调用形态指向 {len(targets)} 个不同函数，无法唯一确定：{detail}")
    target_rva = next(iter(targets))
    return target_rva, sites


def find_weixin_dll(explicit: Optional[str] = None) -> Path:
    """找到微信实际加载的 Weixin.dll。

    顺序：显式指定 → 运行中进程的模块列表（最可靠：这就是要被挂钩的那一份）
    → 注册表里的安装目录 → 常见安装目录。都找不到就报错，由上层提示用户指定。

    注意 Weixin.exe 是启动器，和版本目录里的 Weixin.dll 不在同一层，
    所以不能靠「exe 旁边的 Weixin.dll」来猜。
    """
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise LocatorError(f"指定的 Weixin.dll 不存在：{p}")
        return p

    loaded = _loaded_weixin_dll()
    if loaded is not None:
        return loaded

    candidates: list[Path] = []
    install = _registry_install_path()
    if install is not None:
        candidates.extend(_dlls_under(install))
    for base in (
        Path(r"C:\Program Files\Tencent\Weixin"),
        Path(r"C:\Program Files (x86)\Tencent\Weixin"),
    ):
        candidates.extend(_dlls_under(base))

    if not candidates:
        raise LocatorError(
            "找不到 Weixin.dll：微信没有在运行，注册表与常见安装目录里也没有。"
            "请在配置里手动指定 weixin_dll 路径"
        )

    def sort_key(p: Path) -> tuple:
        return tuple(int(x) if x.isdigit() else -1 for x in p.parent.name.split("."))

    return sorted(set(candidates), key=sort_key)[-1]


def _dlls_under(base: Path) -> list[Path]:
    out: list[Path] = []
    if not base.is_dir():
        return out
    direct = base / "Weixin.dll"
    if direct.is_file():
        out.append(direct)
    out.extend(p for p in base.glob("*/Weixin.dll") if p.is_file())
    return out


def _registry_install_path() -> Optional[Path]:
    try:
        import winreg
    except ImportError:
        return None
    for root, key in (
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin"),
    ):
        try:
            with winreg.OpenKey(root, key) as h:
                value, _type = winreg.QueryValueEx(h, "InstallPath")
        except OSError:
            continue
        p = Path(str(value))
        if p.is_dir():
            return p
    return None


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


def _k32():
    """kernel32，顺手把用到的参数类型定死——64 位下句柄不能当 int 传。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def _weixin_pids() -> list[int]:
    k32 = _k32()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE or not snap:
        return []
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    pids: list[int] = []
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == "weixin.exe":
                pids.append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return pids


def _loaded_weixin_dll() -> Optional[Path]:
    """从正在运行的微信进程的模块列表里取 Weixin.dll 的路径。"""
    pids = _weixin_pids()
    if not pids:
        return None
    k32 = _k32()
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EnumProcessModulesEx.restype = wintypes.BOOL
    psapi.EnumProcessModulesEx.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    ]
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    psapi.GetModuleFileNameExW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    LIST_MODULES_ALL = 0x03
    for pid in pids:
        handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            continue
        try:
            needed = wintypes.DWORD()
            if not psapi.EnumProcessModulesEx(
                handle, None, 0, ctypes.byref(needed), LIST_MODULES_ALL
            ):
                continue
            count = needed.value // ctypes.sizeof(ctypes.c_void_p)
            if count <= 0:
                continue
            mods = (ctypes.c_void_p * count)()
            if not psapi.EnumProcessModulesEx(
                handle, mods, ctypes.sizeof(mods), ctypes.byref(needed), LIST_MODULES_ALL
            ):
                continue
            for h in mods:
                buf = ctypes.create_unicode_buffer(MAX_PATH * 4)
                if not psapi.GetModuleFileNameExW(handle, h, buf, len(buf)):
                    continue
                p = Path(buf.value)
                if p.name.lower() == "weixin.dll" and p.is_file():
                    return p
        finally:
            k32.CloseHandle(handle)
    log.warning("微信进程在跑，但没在它们的模块列表里找到 Weixin.dll")
    return None


def resolve(dll_path: Optional[str] = None, manual_rva: int = 0) -> HookPoint:
    """完整定位流程：找 DLL → 解析 → 扫调用形态 → 返回挂钩点。

    manual_rva 非 0 时跳过签名扫描，直接用它所在的函数。微信改版让自动定位失手时，
    用户在配置里填一个地址就能继续用，不必等本程序适配新版本。给的地址不必正好是
    函数开头，落在函数里即可——按 .pdata 归并到函数边界。
    """
    dll = find_weixin_dll(dll_path)
    image = PeImage(dll)
    digest = hashlib.sha256(image.data).hexdigest()
    version = _file_version(dll)

    if manual_rva:
        fn = image.func_of(manual_rva)
        if fn is None:
            raise LocatorError(
                f"手动指定的地址 {manual_rva:#x} 不落在 Weixin.dll 的任何函数里。"
                f"请核对后重填，或清空该项恢复自动定位。"
            )
        hook = HookPoint(
            rva=fn[0],
            size=fn[1] - fn[0],
            dll_path=str(dll),
            dll_sha256=digest,
            file_version=version,
        )
        log.info("按手动指定的地址定位：%s", hook.describe())
        return hook

    target, sites = scan_key_setter(image)
    fn = image.func_of(target)
    assert fn is not None
    hook = HookPoint(
        rva=fn[0],
        size=fn[1] - fn[0],
        dll_path=str(dll),
        dll_sha256=digest,
        file_version=version,
    )
    log.info("定位成功：%s，调用点 %d 处", hook.describe(), len(sites))
    return hook
