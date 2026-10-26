"""数据目录的占用统计与显式清理。

为什么要有这一层：面板要能看清 ``data`` 目录里每一类东西各占多少，并且**由人按按钮**
才清。每类东西能不能清、清完会发生什么，都写在各自的函数里：

- 图片解码缓存：删掉只是下次再看时重新解码，收消息不受影响。
- 微信数据缓存：那是解密副本。删掉之后必须立刻重新解一遍（``refresh(force=True)``），
  否则 ``_con()`` 会因为文件不在而报「尚未解密」——消息通道会一直断到重启。
- 消息存档：``message_id`` 是发给客户端的长期契约（见 ob_store 模块说明），
  只能由使用者显式清，这里不设任何后台自动清理。
- 日志：轮转交给 ``logging`` 的 handler（``config.LOG_HANDLER``），这里只做
  「立刻轮转一次」和删多余的历史份；正在写入的那一份不动，Windows 上也删不掉。
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import sqlite3
import time
from urllib.parse import quote

from . import config, ob_store, updater, wx_source
from .runtime import RUNTIME

log = logging.getLogger("flandora")

_IMAGE_SUBDIR = "images"


# ---------- 统计 ----------


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _walk_size(path: str, *, skip_dirs: tuple[str, ...] = ()) -> tuple[int, int]:
    """递归统计目录的 (字节数, 文件数)；``skip_dirs`` 里的子目录整棵不数。"""
    if not os.path.isdir(path):
        return 0, 0
    total = files = 0
    for root, dirs, names in os.walk(path):
        if skip_dirs:
            dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in names:
            total += _file_size(os.path.join(root, name))
            files += 1
    return total, files


def _sqlite_rows(path: str) -> int:
    """库里所有表的行数之和（只读打开，不建库、不写一行）。"""
    if not os.path.isfile(path):
        return 0
    total = 0
    try:
        con = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in tables:
            quoted = '"' + str(name).replace('"', '""') + '"'
            try:
                total += int(con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            except (sqlite3.Error, TypeError, ValueError):
                continue
    except sqlite3.Error:
        return total
    finally:
        con.close()
    return total


def work_root() -> str:
    return config.WX_WORK_DIR or wx_source.default_work_dir()


def account_dirs() -> list[str]:
    """缓存根目录下按账号分的子目录（本机只有一个登录账号，但结构如此）。"""
    root = work_root()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return [os.path.join(root, n) for n in sorted(names) if os.path.isdir(os.path.join(root, n))]


def log_stats() -> dict:
    path = config.LOG_FILE
    backups = _rotated_logs()
    files = [{"name": os.path.basename(p), "bytes": _file_size(p)} for p in backups]
    return {
        "path": path,
        "bytes": _file_size(path),
        "rotated_bytes": sum(f["bytes"] for f in files),
        "rotated_files": len(files),
        "files": files,
        "max_mb": config.LOG_MAX_MB,
        "backup_count": config.LOG_BACKUP_COUNT,
    }


def messages_stats() -> dict:
    path = os.path.join(config.data_dir(), "messages.db")
    out = {"path": path, "bytes": _file_size(path), "rows": 0, "oldest": 0, "newest": 0}
    if os.path.isfile(path):
        try:
            out.update(ob_store.message_store().stats())
        except Exception as e:  # noqa: BLE001 - 统计失败不该让整页读不出来
            log.warning("读消息存档统计失败：%s", e)
    return out


def msg_ids_stats() -> dict:
    path = os.path.join(config.data_dir(), "msg_ids.db")
    return {"path": path, "bytes": _file_size(path), "rows": _sqlite_rows(path)}


def images_stats() -> dict:
    total = files = 0
    for d in account_dirs():
        size, count = _walk_size(os.path.join(d, _IMAGE_SUBDIR))
        total += size
        files += count
    return {"bytes": total, "files": files}


def wx_cache_stats() -> dict:
    """解密出来的库（图片缓存在 images 子目录里，单独算）。"""
    total = files = 0
    for d in account_dirs():
        size, count = _walk_size(d, skip_dirs=(_IMAGE_SUBDIR,))
        total += size
        files += count
    return {"bytes": total, "files": files, "root": work_root()}


def updates_stats() -> dict:
    path = updater.updates_dir()
    size, files = _walk_size(path)
    return {"path": path, "bytes": size, "files": files}


def overview() -> dict:
    """整页要用的数字一次算完。"""
    data_dir = config.data_dir()
    logs = log_stats()
    messages = messages_stats()
    msg_ids = msg_ids_stats()
    images = images_stats()
    cache = wx_cache_stats()
    updates = updates_stats()
    total_bytes, total_files = _walk_size(data_dir)
    parts = (
        logs["bytes"]
        + logs["rotated_bytes"]
        + messages["bytes"]
        + msg_ids["bytes"]
        + images["bytes"]
        + cache["bytes"]
        + updates["bytes"]
    )
    return {
        "data_dir": data_dir,
        "total_bytes": total_bytes,
        "total_files": total_files,
        "other_bytes": max(0, total_bytes - parts),
        "logs": logs,
        "messages": messages,
        "msg_ids": msg_ids,
        "images": images,
        "wx_cache": cache,
        "updates": updates,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------- 清理 ----------


def clean_images() -> dict:
    """删图片解码缓存。下次要看同一张图时会重新解，收消息不受影响。"""
    removed = freed = 0
    for d in account_dirs():
        img_dir = os.path.join(d, _IMAGE_SUBDIR)
        if not os.path.isdir(img_dir):
            continue
        try:
            entries = list(os.scandir(img_dir))
        except OSError as e:
            log.warning("读图片缓存目录失败（%s）：%s", img_dir, e)
            continue
        for entry in entries:
            size = entry.stat().st_size if entry.is_file() else 0
            try:
                if entry.is_file():
                    os.remove(entry.path)
                else:
                    shutil.rmtree(entry.path, ignore_errors=True)
            except OSError as e:
                log.warning("删图片缓存失败（%s）：%s", entry.path, e)
                continue
            removed += 1
            freed += size
    return {"removed_files": removed, "freed_bytes": freed, "redecrypt_error": ""}


def clean_wx_cache() -> dict:
    """删解密副本，然后立刻强制重解一遍，免得消息通道一直报「尚未解密」。"""
    removed = freed = 0
    for d in account_dirs():
        for root, dirs, names in os.walk(d):
            dirs[:] = [x for x in dirs if x != _IMAGE_SUBDIR]
            for name in names:
                path = os.path.join(root, name)
                size = _file_size(path)
                try:
                    os.remove(path)
                except OSError as e:
                    log.warning("删微信数据缓存失败（%s）：%s", path, e)
                    continue
                removed += 1
                freed += size

    store = RUNTIME.store
    out = {"removed_files": removed, "freed_bytes": freed, "redecrypt_error": "", "note": ""}
    if store is None:
        out["note"] = "桥接当前没有运行中的收件源，下次启动时会重新解密"
        return out
    try:
        store.refresh(force=True)
    except Exception as e:  # noqa: BLE001 - 重新解密失败要照实回给面板
        log.error("清理缓存后重新解密失败：%s", e)
        out["redecrypt_error"] = f"{type(e).__name__}: {e}"
        return out
    if not store._decrypted:
        out["redecrypt_error"] = "重新解密没有解出任何库，消息通道可能已经断开"
    return out


def clean_updates() -> dict:
    """删已下载待安装的包（含登记文件）。清理后要重新下载。"""
    path = updater.updates_dir()
    removed = freed = 0
    try:
        entries = list(os.scandir(path))
    except OSError as e:
        log.warning("读更新暂存目录失败（%s）：%s", path, e)
        entries = []
    for entry in entries:
        if entry.is_dir():
            size, _ = _walk_size(entry.path)
            shutil.rmtree(entry.path, ignore_errors=True)
            if not os.path.isdir(entry.path):
                removed += 1
                freed += size
        else:
            size = _file_size(entry.path)
            try:
                os.remove(entry.path)
            except OSError as e:
                log.warning("删更新暂存文件失败（%s）：%s", entry.path, e)
                continue
            removed += 1
            freed += size
    updater.clear_pending()
    return {"removed_files": removed, "freed_bytes": freed, "redecrypt_error": ""}


def _backup_index(path: str) -> int:
    """``flandora.log.3`` → 3；认不出来的返回 0，按最老处理。"""
    tail = path.rsplit(".", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def _rotated_logs() -> list:
    """本程序自己轮转出来的历史日志（编号越小越新），按新旧排好。

    只认 ``flandora.log.N``（N 是数字）。手工改名成别的样子的文件不在这里出现，
    也就永远不会被清理动作删掉。
    """
    out = [p for p in glob.glob(config.LOG_FILE + ".*") if p.rsplit(".", 1)[-1].isdigit()]
    return sorted(out, key=_backup_index)


def clean_old_logs() -> dict:
    """删掉超出保留份数的历史日志，留下编号最小（最新）的那几份。

    正在写入的 flandora.log 不在候选里，不会被碰到。
    """
    keep = max(0, int(config.LOG_BACKUP_COUNT))
    backups = _rotated_logs()
    removed = freed = 0
    for path in backups[keep:]:
        size = _file_size(path)
        try:
            os.remove(path)
        except OSError as e:
            log.warning("删历史日志失败（%s）：%s", path, e)
            continue
        removed += 1
        freed += size
    return {"removed_files": removed, "freed_bytes": freed, "redecrypt_error": ""}


def rotate_log() -> dict:
    """立刻轮转一次：当前文件改名成 .1，旧份依次后退，超出的删掉。"""
    handler = config.LOG_HANDLER
    if handler is None:
        return {
            "removed_files": 0,
            "freed_bytes": 0,
            "redecrypt_error": "",
            "note": "日志没有落到文件里（handler 未装配）",
        }
    try:
        handler.doRollover()
    except Exception as e:  # noqa: BLE001 - 轮转失败要说清而不是假装成功
        log.error("日志轮转失败：%s", e)
        return {"removed_files": 0, "freed_bytes": 0, "redecrypt_error": f"{type(e).__name__}: {e}"}
    pruned = clean_old_logs()
    log.info("日志已轮转，清理历史份 %s 个", pruned["removed_files"])
    return {**pruned, "redecrypt_error": "", "note": "已换成新的一份，历史份按保留份数留存"}


# ---------- 消息存档 ----------


def messages_prune(days: int) -> dict:
    """删掉 N 天前的存档。被删掉的 ``message_id`` 以后撤回/引用会取不到原文。"""
    days = max(1, min(int(days), 3650))
    cutoff = time.time() - days * 86400
    before_bytes = _file_size(os.path.join(config.data_dir(), "messages.db"))
    store = ob_store.message_store()
    removed = store.prune_before(cutoff)
    log.info("消息存档清理：删掉 %s 条 %s 天前的记录", removed, days)
    return {
        "removed_rows": removed,
        "days": days,
        "bytes": before_bytes,
        "rows": store.count(),
    }


def messages_clear() -> dict:
    """清空整个存档。"""
    store = ob_store.message_store()
    removed = store.clear()
    log.info("消息存档已清空：%s 条", removed)
    return {"removed_rows": removed, "rows": store.count()}


def messages_vacuum() -> dict:
    """重写库文件，把删掉的行腾出来的空间还给系统。"""
    store = ob_store.message_store()
    before = _file_size(os.path.join(config.data_dir(), "messages.db"))
    store.vacuum()
    after = _file_size(os.path.join(config.data_dir(), "messages.db"))
    return {"bytes_before": before, "bytes_after": after, "freed_bytes": max(0, before - after)}
