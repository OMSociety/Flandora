"""富消息（公众号、B 站、小程序、文件、聊天记录……）解析。"""

from flandora import wx_appmsg, wx_store


def _app(inner: str, extra: str = "") -> str:
    return f'<msg><appmsg appid="" sdkver="0">{inner}</appmsg>{extra}</msg>'


MP_ARTICLE = _app(
    "<title>标题甲</title><des>摘要乙</des><type>5</type>"
    "<url>https://mp.weixin.qq.com/s?__biz=AAA%3D%3D&amp;mid=1&amp;idx=1&amp;sn=abc"
    "&amp;chksm=zz&amp;mpshare=1&amp;scene=1</url>"
    "<sourceusername>gh_80330ed4178a</sourceusername>"
    "<sourcedisplayname>中国新闻网</sourcedisplayname>"
)
BILI = _app(
    "<title>视频题</title><des>UP主：某某\n播放：253.6万</des><type>4</type>"
    "<url>https://b23.tv/AbCd?share_medium=android&amp;buvid=xx</url>",
    "<appinfo><appname>哔哩哔哩</appname></appinfo>",
)
MINIAPP = _app(
    "<title>来杯咖啡</title><type>33</type>"
    "<url>https://mp.weixin.qq.com/mp/waerrpage?appid=wx21c70e3c07a1b2d3&amp;type=upgrade</url>"
    "<sourcedisplayname>某咖啡</sourcedisplayname>"
    "<weappinfo><pagepath>pages/index.html</pagepath><username>gh_x@app</username>"
    "<appid>wx21c70e3c07a1b2d3</appid></weappinfo>"
)
FILE = _app(
    "<title>论文.docx</title><type>6</type>"
    "<appattach><totallen>195976</totallen><fileext>docx</fileext></appattach>"
    "<md5>2450f5f1</md5>"
)
RECORD = _app(
    "<title>群聊的聊天记录</title><type>19</type><recorditem><![CDATA[<recordinfo><datalist>"
    '<dataitem datatype="1"><sourcename>甲</sourcename><datadesc>你好</datadesc></dataitem>'
    '<dataitem datatype="2"><sourcename>乙</sourcename></dataitem>'
    '<dataitem datatype="8"><sourcename>乙</sourcename><datatitle>a.pdf</datatitle></dataitem>'
    "</datalist></recordinfo>]]></recorditem>"
)


def test_public_account_article_keeps_only_locating_params():
    card = wx_appmsg.parse_appmsg(5, MP_ARTICLE)
    assert card.label == "公众号文章"
    assert card.source == "中国新闻网"
    assert card.url == "https://mp.weixin.qq.com/s?__biz=AAA%3D%3D&mid=1&idx=1&sn=abc"
    assert card.to_dict()["account"] == "gh_80330ed4178a"


def test_bilibili_share_drops_the_query_and_takes_source_from_appinfo():
    card = wx_appmsg.parse_appmsg(4, BILI)
    assert card.url == "https://b23.tv/AbCd"
    assert card.source == "哔哩哔哩"
    assert "UP主：某某" in card.text()


def test_miniapp_hides_the_upgrade_placeholder_and_reports_page():
    card = wx_appmsg.parse_appmsg(33, MINIAPP)
    assert card.label == "小程序"
    assert card.url == ""
    assert card.to_dict()["pagepath"] == "pages/index.html"
    assert card.to_dict()["appid"] == "wx21c70e3c07a1b2d3"


def test_file_card_shows_name_and_size():
    card = wx_appmsg.parse_appmsg(6, FILE)
    assert card.text().splitlines()[:2] == ["[文件] 论文.docx", "191.4 KB"]


def test_chat_record_expands_to_speaker_lines():
    card = wx_appmsg.parse_appmsg(19, RECORD)
    assert card.lines == ("甲: 你好", "乙: [图片]", "乙: [文件] a.pdf")


def test_xml_with_entities_is_refused():
    evil = '<!DOCTYPE x [<!ENTITY a "b">]><msg><appmsg><title>t</title></appmsg></msg>'
    assert wx_appmsg.parse_appmsg(5, evil) is None


def test_garbage_body_is_none():
    assert wx_appmsg.parse_appmsg(5, "not xml") is None


def test_classify_routes_cards_and_emoticon_appmsg():
    kind, text = wx_store.classify(49 | (5 << 32), MP_ARTICLE)
    assert kind == "link" and text.startswith("[公众号文章] 标题甲")
    kind, text = wx_store.classify(49 | (19 << 32), RECORD)
    assert kind == "merged" and "甲: 你好" in text
    sticker = _app(
        "<title>x</title><type>8</type><appattach><emoticonmd5>"
        + "A" * 32
        + "</emoticonmd5></appattach>"
    )
    kind, text = wx_store.classify(49 | (8 << 32), sticker)
    assert kind == "emoji" and text.endswith('md5="' + "a" * 32 + '"')


def test_location_and_namecard():
    loc = '<msg><location x="30.1" y="120.2" label="某路1号" poiname="某地"/></msg>'
    kind, text = wx_store.classify(48, loc)
    assert kind == "location" and text.splitlines() == ["[位置] 某地", "某路1号"]
    card = '<msg username="wxid_abc" nickname="张三" alias="zs"/>'
    kind, text = wx_store.classify(42, card)
    assert kind == "card" and text == "[名片] 张三"


def test_voice_video_placeholders_carry_duration_and_size():
    _, voice = wx_store.classify(34, '<msg><voicemsg voicelength="2233" length="3399"/></msg>')
    assert voice == "[语音] 2 秒"
    _, video = wx_store.classify(43, '<msg><videomsg playlength="20" length="1450741"/></msg>')
    assert video == "[视频] 20 秒 1.4 MB"
    assert wx_store.classify(34, "x")[1] == "[语音]"


def test_payload_carries_card_dict():
    payload = wx_store.payload_of(
        local_id=1,
        server_id=2,
        local_type=49 | (4 << 32),
        sender="s",
        create_time=1,
        body=BILI,
    )
    assert payload["card"]["url"] == "https://b23.tv/AbCd"
