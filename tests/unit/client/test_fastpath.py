import http.cookiejar

from botonomus.client.fastpath import cookie_from_jar, impersonation_target, jar_cookie

TARGETS = ["chrome99", "chrome136", "chrome142", "chrome146", "chrome150", "chrome133a",
           "chrome131_android", "safari17_0", "edge101"]  # fmt: skip


def test_newest_target_not_newer_than_browser():
    assert impersonation_target(154, TARGETS) == "chrome150"
    assert impersonation_target(146, TARGETS) == "chrome146"
    assert impersonation_target(145, TARGETS) == "chrome142"


def test_no_target_when_too_old_or_missing():
    assert impersonation_target(154, ["chrome99", "chrome120"]) is None
    assert impersonation_target(154, []) is None
    assert impersonation_target(98, TARGETS) is None


def test_cookie_round_trip_between_cdp_and_jar():
    cdp = {"name": "sid", "value": "v1", "domain": ".example.com", "path": "/", "secure": True,
           "httpOnly": True, "expires": 1999999999.0}  # fmt: skip
    cookie = jar_cookie(cdp)
    assert isinstance(cookie, http.cookiejar.Cookie)
    assert (cookie.name, cookie.value, cookie.domain, cookie.secure) == (
        "sid",
        "v1",
        ".example.com",
        True,
    )
    assert cookie_from_jar(cookie) == {"name": "sid", "value": "v1", "domain": ".example.com",
                                       "path": "/", "secure": True, "httpOnly": True,
                                       "expires": 1999999999}  # fmt: skip


def test_session_cookie_has_no_expiry():
    cookie = jar_cookie({"name": "a", "value": "b", "domain": "x.example", "path": "/",
                         "secure": False, "expires": -1})  # fmt: skip
    assert cookie.expires is None
    assert "expires" not in cookie_from_jar(cookie)


from botonomus.client.fastpath import cookie_changes  # noqa: E402


def sent_cookie(**extra):
    base = {"name": "sid", "value": "1", "domain": "shop.example", "path": "/",
            "secure": True, "httpOnly": True, "sameSite": "None", "priority": "High",
            "expires": 1999999999.0}  # fmt: skip
    return {**base, **extra}


def test_unchanged_cookies_are_not_written_back():
    sent = [sent_cookie()]
    assert cookie_changes(sent, [jar_cookie(c) for c in sent]) == []


def test_changed_cookie_keeps_its_browser_attributes():
    sent = [sent_cookie()]
    after = [jar_cookie(sent_cookie(value="2"))]
    assert cookie_changes(sent, after) == [
        {
            "name": "sid",
            "value": "2",
            "domain": "shop.example",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "None",
            "priority": "High",
            "expires": 1999999999,
        }  # fmt: skip
    ]


def test_new_cookie_is_added_and_deleted_cookie_expires():
    sent = [sent_cookie()]
    new = jar_cookie({"name": "n", "value": "v", "domain": "shop.example", "path": "/"})
    changes = cookie_changes(sent, [new])
    assert {"name": "n", "value": "v", "domain": "shop.example", "path": "/", "secure": False,
            "httpOnly": False} in changes  # fmt: skip
    deleted = [c for c in changes if c["name"] == "sid"]
    assert deleted == [{**{k: v for k, v in sent_cookie().items() if k != "expires"},
                        "expires": 1}]  # fmt: skip
