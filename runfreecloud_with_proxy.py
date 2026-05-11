#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Runfreecloud 自动签到 + 续期（代理版）
修复：从登录页 HTML 中提取 base64 验证码，避免 403
"""

import requests
import ddddocr
import re
import os
import time
import base64
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ══════════════════════════════════════════════════
# 配置区
# ══════════════════════════════════════════════════
EMAIL    = os.environ.get("RFC_EMAIL",    "你的邮箱@gmail.com")
PASSWORD = os.environ.get("RFC_PASSWORD", "你的密码")
PUSHPLUS_TOKEN = os.environ.get("RFC_PUSHPLUS_TOKEN", "")

# 手动代理（优先）格式 "host:port"，不需要可留空
MANUAL_PROXIES: list[str] = [
    # "1.2.3.4:1080",
]

BASE_URL          = "https://run.freecloud.ltd"
PROXY_TEST_URL    = "https://run.freecloud.ltd"
PROXY_TIMEOUT     = 10      # 单个代理测试超时（秒）
PROXY_WORKERS     = 30      # 并发测试线程数
TOP_N             = 5       # 取最快的N个代理
MAX_LOGIN_RETRY   = 5       # 登录验证码重试次数
RENEW_DAYS_BEFORE = 1       # 到期前几天续期
# ══════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────
# 1. 抓取国内代理
# ──────────────────────────────────────────────────

def fetch_cn_proxies() -> list[str]:
    """从多个免费 API 抓取 CN socks5 代理，去重后返回"""
    proxies: set[str] = set()

    sources = [
        (
            "https://api.proxyscrape.com/v3/free-proxy-list/get"
            "?request=displayproxies&protocol=socks5&timeout=5000"
            "&country=CN&simplified=true",
            "text"
        ),
        (
            "https://proxylist.geonode.com/api/proxy-list"
            "?limit=200&page=1&sort_by=speed&sort_type=asc"
            "&protocols=socks5&country=CN",
            "geonode"
        ),
        (
            "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main"
            "/proxies/protocols/socks5/data.txt",
            "text"
        ),
        (
            "https://cdn.jsdelivr.net/gh/fate0/proxylist@master/proxy.list",
            "fate0"
        ),
    ]

    for url, mode in sources:
        try:
            r = requests.get(url, timeout=12)
            if r.status_code != 200:
                continue

            if mode == "geonode":
                items = r.json().get("data", [])
                for item in items:
                    ip   = item.get("ip", "")
                    port = item.get("port", "")
                    if ip and port:
                        proxies.add(f"{ip}:{port}")
                log.info(f"geonode CN → {len(items)} 条")

            elif mode == "fate0":
                import json as _json
                added = 0
                for line in r.text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = _json.loads(line)
                        if (obj.get("type") == "socks5"
                                and obj.get("country", "").upper() == "CN"):
                            proxies.add(f"{obj['host']}:{obj['port']}")
                            added += 1
                    except Exception:
                        pass
                log.info(f"fate0 CN → {added} 条")

            else:  # text
                found = re.findall(
                    r"\b(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})\b", r.text
                )
                proxies.update(found)
                log.info(f"{url[:55]}... → {len(found)} 条")

        except Exception as e:
            log.debug(f"抓取失败 {url[:55]}: {e}")

    result = list(proxies)
    log.info(f"共抓取到 {len(result)} 个候选国内代理")
    return result


# ──────────────────────────────────────────────────
# 2. 测速 & 筛选
# ──────────────────────────────────────────────────

def test_one_proxy(proxy: str) -> tuple[str, float] | None:
    host, port = proxy.rsplit(":", 1)
    proxies = {
        "http":  f"socks5h://{host}:{port}",
        "https": f"socks5h://{host}:{port}",
    }
    try:
        t0 = time.monotonic()
        r  = requests.get(
            PROXY_TEST_URL,
            proxies=proxies,
            timeout=PROXY_TIMEOUT,
            allow_redirects=True,
        )
        elapsed = time.monotonic() - t0
        if r.status_code in (200, 301, 302, 403):
            return (proxy, elapsed)
    except Exception:
        pass
    return None


def get_top_proxies(candidates: list[str]) -> list[str]:
    if not candidates:
        return []

    log.info(f"开始并发测速（{PROXY_WORKERS} 线程，共 {len(candidates)} 个）...")
    results: list[tuple[str, float]] = []

    with ThreadPoolExecutor(max_workers=PROXY_WORKERS) as ex:
        futures = {ex.submit(test_one_proxy, p): p for p in candidates}
        done = 0
        for fut in as_completed(futures):
            done += 1
            res = fut.result()
            if res:
                proxy, elapsed = res
                results.append(res)
                log.info(
                    f"  ✅ {proxy:<22} {elapsed:.2f}s"
                    f"  （已测 {done}/{len(futures)}，可用 {len(results)}）"
                )

    if not results:
        log.warning("没有找到任何可用代理")
        return []

    results.sort(key=lambda x: x[1])
    top = results[:TOP_N]

    log.info("━" * 45)
    log.info(f"最快 {TOP_N} 个代理（均可访问目标网站）：")
    for i, (p, t) in enumerate(top, 1):
        log.info(f"  #{i}  {p:<22}  {t:.2f}s")
    log.info("━" * 45)

    return [p for p, _ in top]


# ──────────────────────────────────────────────────
# 3. 主业务逻辑
# ──────────────────────────────────────────────────

def make_session(proxy: str | None) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": BASE_URL,
        "Origin":  BASE_URL,
    })
    if proxy:
        host, port = proxy.rsplit(":", 1)
        sess.proxies.update({
            "http":  f"socks5h://{host}:{port}",
            "https": f"socks5h://{host}:{port}",
        })
        log.info(f"当前代理：{proxy}")
    return sess


class Bot:
    def __init__(self, proxy: str | None = None):
        self.sess = make_session(proxy)
        self.ocr  = ddddocr.DdddOcr(show_ad=False)

    # ── 登录（修复版） ──
    def _captcha(self) -> bytes:
        """从登录页 HTML 中提取 base64 验证码图片，并保存到 /tmp 用于调试"""
        r = self.sess.get(f"{BASE_URL}/login", timeout=15)
        if r.status_code != 200:
            with open("/tmp/login_page_error.html", "w", encoding="utf-8") as f:
                f.write(r.text)
            raise Exception(f"登录页返回 {r.status_code}")

        # 正则提取 data:image 的 base64
        match = re.search(
            r'<img[^>]+src="data:image/[^;]+;base64,([^"]+)"',
            r.text
        )
        if not match:
            # 保存页面以供分析
            with open("/tmp/login_page_no_captcha.html", "w", encoding="utf-8") as f:
                f.write(r.text)
            raise Exception("登录页中未找到验证码图片，请查看 /tmp/login_page_no_captcha.html")

        img_b64 = match.group(1)
        # 补齐可能缺失的 base64 填充
        missing_padding = len(img_b64) % 4
        if missing_padding:
            img_b64 += '=' * (4 - missing_padding)
        img_bytes = base64.b64decode(img_b64)

        # 保存验证码图片（方便人工查看）
        with open("/tmp/captcha_img.png", "wb") as f:
            f.write(img_bytes)

        return img_bytes

    def _ocr(self, img: bytes) -> str:
        raw   = self.ocr.classification(img)
        clean = re.sub(r"[^0-9a-zA-Z]", "", raw)
        log.info(f"验证码识别：{raw!r} → {clean!r}")
        return clean

    def login(self) -> bool:
        for i in range(1, MAX_LOGIN_RETRY + 1):
            log.info(f"登录 {i}/{MAX_LOGIN_RETRY}")
            try:
                captcha_img = self._captcha()
                captcha_code = self._ocr(captcha_img)
                log.info(f"识别结果: {captcha_code}")

                # 再次请求登录页以获取 token（部分网站需要）
                r = self.sess.get(f"{BASE_URL}/login", timeout=15)
                token_match = re.search(r'name="token" value="([^"]+)"', r.text)
                token = token_match.group(1) if token_match else ""

                # 构造表单数据
                payload = {
                    "email": EMAIL,
                    "password": PASSWORD,
                    "captcha": captcha_code,
                    "token": token,
                }
                # 提交登录
                r = self.sess.post(
                    f"{BASE_URL}/login",
                    data=payload,
                    allow_redirects=True,
                    timeout=15,
                )
                # 判断登录成功
                if "clientarea" in r.url or "用户中心" in r.text:
                    log.info("✅ 登录成功")
                    return True
                else:
                    log.warning(f"登录失败，状态码:{r.status_code}，URL:{r.url}")
                    # 保存响应片段用于调试
                    with open("/tmp/login_fail.html", "w", encoding="utf-8") as f:
                        f.write(r.text[:2000])
            except Exception as e:
                log.error(f"登录异常: {e}")
                try:
                    with open("/tmp/login_error.html", "w", encoding="utf-8") as f:
                        f.write(r.text[:2000])
                except:
                    pass
            time.sleep(2)
        return False

    # ── 签到（沿用原脚本逻辑） ──
    @staticmethod
    def _solve(expr: str) -> str:
        e = expr.replace(" ", "")
        if re.match(r"^[\d+\-*/().]+$", e):
            try:
                v = eval(e)
                if isinstance(v, float) and v == int(v):
                    return str(int(v))
                return f"{v:.6f}".rstrip("0").rstrip(".")
            except Exception:
                pass
        return ""

    def checkin(self) -> bool:
        try:
            r        = self.sess.get(f"{BASE_URL}/api/user/checkIn/math", timeout=15)
            question = (r.json().get("data") or {}).get("question", "")
            log.info(f"签到题：{question}")
            if not question:
                return False
            answer = self._solve(question)
            log.info(f"答案：{answer}")
            r2  = self.sess.post(
                f"{BASE_URL}/api/user/checkIn",
                json={"captcha": answer},
                timeout=15,
            )
            msg = r2.json().get("message", "")
            if r2.json().get("data") is not None or "成功" in msg:
                log.info(f"✅ 签到成功：{msg}")
                return True
            if "已经签到" in msg or "already" in msg.lower():
                log.info(f"ℹ️ 今日已签到：{msg}")
                return True
            log.warning(f"签到失败：{msg}")
            return False
        except Exception as e:
            log.error(f"签到异常：{e}")
            return False

    # ── 续期（沿用原脚本逻辑） ──
    def _services(self) -> list:
        for ep in [
            "/api/user/service/fetch",
            "/api/client/service/list",
            "/api/user/server/list",
        ]:
            try:
                r = self.sess.get(f"{BASE_URL}{ep}", timeout=15)
                if r.status_code != 200:
                    continue
                d    = r.json()
                svcs = (d.get("data") or {}).get("data") or d.get("data") or []
                if isinstance(svcs, list) and svcs:
                    log.info(f"服务列表（{ep}）：{len(svcs)} 个")
                    return svcs
            except Exception:
                pass
        return []

    def _renew(self, svc_id: str) -> bool:
        try:
            r1  = self.sess.post(
                f"{BASE_URL}/api/user/order/save",
                json={"service_id": svc_id, "period": 1},
                timeout=15,
            )
            oid = (
                (r1.json().get("data") or {}).get("order_id")
                or (r1.json().get("data") or {}).get("id")
            )
            if not oid:
                log.warning(f"建单失败：{r1.json().get('message')}")
                return False
            r2  = self.sess.post(
                f"{BASE_URL}/api/user/order/checkout",
                json={"trade_no": str(oid), "method": "balance"},
                timeout=15,
            )
            msg = r2.json().get("message", "")
            if r2.json().get("data") is not None or "成功" in msg:
                log.info(f"✅ 续期成功：{msg}")
                return True
            log.warning(f"续期失败：{msg}")
            return False
        except Exception as e:
            log.error(f"续期异常：{e}")
            return False

    def check_and_renew(self) -> list:
        results = []
        now      = datetime.now()
        deadline = now + timedelta(days=RENEW_DAYS_BEFORE)
        for svc in self._services():
            try:
                svc_id = str(svc.get("id", ""))
                name   = svc.get("name", svc_id)
                ip     = svc.get("ip", "-")
                raw    = (svc.get("expired_at")
                          or svc.get("expire_at")
                          or svc.get("end_time"))
                if not raw:
                    continue
                if isinstance(raw, int):
                    expire = datetime.fromtimestamp(raw)
                else:
                    expire = None
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            expire = datetime.strptime(str(raw), fmt)
                            break
                        except ValueError:
                            pass
                    if not expire:
                        log.warning(f"{name} 时间格式未知：{raw}")
                        continue

                days_left = (expire - now).days
                log.info(f"[{name}] {ip}  到期:{expire:%Y-%m-%d}  剩余:{days_left}天")

                if expire <= deadline:
                    log.info(f"⚡ [{name}] 即将到期，续期中...")
                    ok = self._renew(svc_id)
                    results.append({
                        "name": name, "ip": ip,
                        "expire": expire.strftime("%Y-%m-%d"),
                        "renewed": ok,
                    })
                else:
                    log.info(f"[{name}] 无需续期")
            except Exception as e:
                log.error(f"处理服务出错：{e}")
        return results


# ──────────────────────────────────────────────────
# 4. 入口
# ──────────────────────────────────────────────────

def send_notify(title: str, body: str):
    if not PUSHPLUS_TOKEN:
        return
    try:
        requests.post(
            "https://www.pushplus.plus/send",
            json={"token": PUSHPLUS_TOKEN, "title": title, "content": body},
            timeout=10,
        )
    except Exception:
        pass


def try_run(proxy: str | None) -> tuple[bool, list] | None:
    bot = Bot(proxy=proxy)
    if not bot.login():
        return None
    return bot.checkin(), bot.check_and_renew()


def main():
    log.info("═" * 50)
    log.info(f"开始  {datetime.now():%Y-%m-%d %H:%M:%S}")

    # ── 先直连试一次 ──
    log.info("▶ 尝试直连...")
    result = try_run(None)

    # ── 直连失败 → 构建代理池 ──
    if result is None:
        log.info("直连失败，开始构建代理池...")

        candidates = list(MANUAL_PROXIES)
        if len(candidates) < TOP_N:
            candidates += fetch_cn_proxies()
        candidates = list(dict.fromkeys(candidates))

        top_proxies = get_top_proxies(candidates)

        if not top_proxies:
            msg = "❌ 未找到可用代理，任务终止"
            log.error(msg)
            send_notify("Runfreecloud 失败", msg)
            return

        result = None
        for proxy in top_proxies:
            log.info(f"▶ 尝试代理 {proxy}...")
            result = try_run(proxy)
            if result is not None:
                log.info(f"✅ 代理 {proxy} 完整流程成功")
                break
            log.warning(f"代理 {proxy} 登录失败，换下一个")

        if result is None:
            msg = "❌ Top5 代理均登录失败"
            log.error(msg)
            send_notify("Runfreecloud 失败", msg)
            return

    checkin_ok, renew_results = result

    lines = [f"📅 {datetime.now():%Y-%m-%d %H:%M}"]
    lines.append(f"{'✅' if checkin_ok else '❌'} 签到：{'成功' if checkin_ok else '失败'}")
    if renew_results:
        for r in renew_results:
            s = "✅ 续期成功" if r["renewed"] else "❌ 续期失败"
            lines.append(f"{s}：{r['name']} ({r['ip']}) 到期:{r['expire']}")
    else:
        lines.append("ℹ️ 无需续期")

    summary = "\n".join(lines)
    log.info("\n" + summary)
    send_notify("Runfreecloud 完成", summary)
    log.info("═" * 50)


if __name__ == "__main__":
    main()
