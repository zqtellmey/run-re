import asyncio, os, re, logging, random, base64, json, math
from pathlib import Path
from datetime import datetime, timedelta
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
BASE_URL = "https://run.freecloud.ltd"
LOGIN_URL = f"{BASE_URL}/login"
USER_CENTER = f"{BASE_URL}/clientarea"
SIGN_PAGE = f"{BASE_URL}/addons?_plugin=5&controller=index&action=index"

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ---------- WxPusher 推送 ----------
WXPUSHER_TOKEN = os.environ.get("WXPUSHER_TOKEN", "")
WXPUSHER_UID   = os.environ.get("WXPUSHER_UID", "")

def wxpush(content: str):
    if not WXPUSHER_TOKEN or not WXPUSHER_UID:
        log.warning("📨 WXPUSHER_TOKEN 或 WXPUSHER_UID 未配置，跳过推送")
        return
    import urllib.request
    payload = json.dumps({
        "appToken": WXPUSHER_TOKEN,
        "content": content,
        "contentType": 1,
        "uids": [WXPUSHER_UID],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                log.info("📨 WxPusher 推送成功")
            else:
                log.warning(f"📨 WxPusher 推送失败: {result}")
    except Exception as e:
        log.warning(f"📨 WxPusher 推送异常: {e}")

ocr = ddddocr.DdddOcr(beta=True, show_ad=False)

# ---------- 工具函数 ----------
async def take_screenshot(browser, tab, name):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{ts}_{name}.png")
        await tab.take_screenshot(path=path)
        log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败: {e}")

async def get_text(tab):
    try:
        result = await tab.execute_script("return document.body.innerText")
        if isinstance(result, dict):
            return result.get("result", {}).get("result", {}).get("value", "")
        return str(result)
    except:
        return ""

async def get_url(tab):
    try:
        result = await tab.execute_script("return window.location.href")
        if isinstance(result, dict):
            return result.get("result", {}).get("result", {}).get("value", "")
        return str(result)
    except:
        return ""

async def human_delay(min_s=0.3, max_s=0.8):
    await asyncio.sleep(random.uniform(min_s, max_s))

async def wait_for_url_contains(tab, keyword, timeout=10):
    for _ in range(timeout * 2):
        url = await get_url(tab)
        if keyword in url:
            return True
        await asyncio.sleep(0.5)
    return False

async def wait_for_element_by_text(tab, text, timeout=10):
    for _ in range(timeout * 2):
        body = await get_text(tab)
        if text in body:
            return True
        await asyncio.sleep(0.5)
    return False

# ---------- 浏览器管理 ----------
def _find_chromium() -> str | None:
    candidates = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            log.info(f"找到 Chromium: {p}")
            return p
    import subprocess
    try:
        result = subprocess.run(["which", "chromium-browser"], capture_output=True, text=True, timeout=5)
        path = result.stdout.strip()
        if path and os.path.isfile(path):
            return path
    except:
        pass
    return None

async def create_browser():
    opts = ChromiumOptions()
    opts.headless = False
    path = _find_chromium()
    if path:
        opts.binary_location = path

    opts.add_argument("--window-size=1280,720")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--exclude-switches=enable-automation")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    opts.add_argument("--disable-save-password-bubble")
    opts.add_argument("--disable-password-generation")
    opts.add_argument("--password-store=basic")
    opts.add_argument("--use-mock-keychain")

    opts.browser_preferences = {
        "credentials_enable_service": False,
        "credentials_enable_autosign": False,
        "profile": {
            "password_manager_enabled": False,
            "default_content_setting_values": {
                "notifications": 2,
                "geolocation": 2,
            },
        },
        "autofill": {"enabled": False},
        "intl": {"accept_languages": "zh-CN,zh,en-US,en"},
    }

    browser = await Chrome(options=opts).__aenter__()
    tab = await browser.start()

    # 注入指纹伪装
    try:
        await tab.execute_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
    except:
        pass

    return browser, tab

# ---------- Cloudflare 手动点击（保留原逻辑）----------
async def manual_cf_click(tab, timeout=15):
    log.info("尝试手动完成 Cloudflare 验证（Shadow DOM 穿透点击）...")
    for i in range(timeout):
        body = await get_text(tab)
        if "email" in body or "登录" in body or "请输入邮箱" in body or "用户中心" in body:
            log.info("✅ Cloudflare 验证已通过")
            return True
        try:
            shadow_roots = await tab.find_shadow_roots(deep=False)
            cf_shadow = None
            for sr in shadow_roots:
                try:
                    html = await sr.inner_html
                    if "challenges.cloudflare.com" in html:
                        cf_shadow = sr
                        break
                except:
                    pass
            if cf_shadow is None:
                await asyncio.sleep(1)
                continue
            iframe_el = await cf_shadow.query('iframe[src*="challenges.cloudflare.com"]', timeout=3)
            body_el = await iframe_el.find(tag_name="body", timeout=3)
            inner_shadow = await body_el.get_shadow_root(timeout=3)
            checkbox = await inner_shadow.query("span.cb-i", timeout=3)
            await checkbox.click()
            log.info("已点击 Cloudflare checkbox，等待验证...")
            await asyncio.sleep(3)
            body2 = await get_text(tab)
            if "email" in body2 or "登录" in body2 or "请输入邮箱" in body2 or "用户中心" in body2:
                log.info("✅ 点击后验证通过")
                return True
        except Exception as e:
            log.info(f"第{i+1}s: {e}")
        await asyncio.sleep(1)
    log.error("Cloudflare 验证超时")
    return False

async def ensure_cf_passed(tab, url, timeout=15):
    """确保 CF 验证通过，返回 True/False"""
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(url)
    except:
        await tab.go_to(url)
    for _ in range(timeout):
        body = await get_text(tab)
        if "verify you are human" not in body.lower() and "cloudflare" not in body.lower():
            return True
        await asyncio.sleep(1)
    return await manual_cf_click(tab)

# ---------- 验证码处理 ----------
async def fill_captcha(tab):
    for _ in range(3):
        cap_img = None
        try:
            cap_img = await tab.find(id="allow_login_email_captcha", timeout=5)
        except:
            pass
        if not cap_img:
            try:
                cap_img = await tab.find(tag_name="img", alt="验证码", timeout=5)
            except:
                pass
        if cap_img:
            src = cap_img.get_attribute("src")
            if src and src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                raw = ocr.classification(img_bytes)
                code = re.sub(r'[^0-9]', '', raw)
                log.info(f"识别验证码: {code}")
                await tab.execute_script(f"""
                    (function() {{
                        var input =
                            document.querySelector('#captcha_allow_login_email_captcha') ||
                            document.querySelector('input[name="captcha"]') ||
                            document.querySelector('input[placeholder*="验证码"]');
                        if (input) {{
                            input.focus();
                            input.value = '{code}';
                            input.dispatchEvent(new Event('input', {{bubbles:true}}));
                            input.dispatchEvent(new Event('change', {{bubbles:true}}));
                        }}
                    }})()
                """)
                return code
        await asyncio.sleep(1)
    return ""

# ---------- 登录 ----------
async def login(browser, tab, max_retries=3):
    for attempt in range(1, max_retries + 1):
        log.info(f"登录 {attempt}/{max_retries}")
        if not await ensure_cf_passed(tab, LOGIN_URL):
            log.error("CF 验证失败，重试登录")
            continue

        # 填写邮箱密码
        try:
            email_el = await tab.find(tag_name="input", name="email", timeout=5)
        except:
            email_el = await tab.find(placeholder="请输入邮箱地址", timeout=5)
        await email_el.click()
        await email_el.type_text(EMAIL, humanize=True)
        await human_delay()

        try:
            pass_el = await tab.find(tag_name="input", name="password", timeout=5)
        except:
            pass_el = await tab.find(placeholder="请输入登录密码", timeout=5)
        await pass_el.click()
        await pass_el.type_text(PASSWORD, humanize=True)

        captcha = await fill_captcha(tab)
        if not captcha:
            log.warning("验证码识别失败，重试")
            continue

        try:
            login_btn = await tab.query("button.btn.btn-primary", timeout=5)
        except:
            login_btn = await tab.find(tag_name="button", text="登录", timeout=5)
        await login_btn.click()
        log.info("已点击登录，检查跳转...")

        if await wait_for_url_contains(tab, "/clientarea", 8):
            log.info("✅ 登录成功")
            await take_screenshot(browser, tab, "02_login_success")
            return True

        log.warning("登录后未跳转，重试")
    return False

# ---------- 签到 ----------
async def sign(browser, tab):
    log.info("前往签到页...")
    if not await ensure_cf_passed(tab, SIGN_PAGE):
        log.warning("签到页 CF 验证失败，可能已签到")
        return None

    if not await wait_for_element_by_text(tab, "我要签到", 10):
        body = await get_text(tab)
        if "已签到" in body or "今日已" in body:
            log.info("今日已签到")
        else:
            log.warning(f"未找到签到按钮, 片段: {body[:200]}")
        await take_screenshot(browser, tab, "02_sign_check")
        return None

    btn = await tab.find(tag_name="button", text="我要签到", timeout=5)
    await btn.click()
    log.info("已点击'我要签到'")
    await asyncio.sleep(1)

    text = await get_text(tab)
    match = re.search(r'请计算[：:]\s*(\d+)\s*([+\-*/])\s*(\d+)', text)
    if match:
        a, op, b = int(match[1]), match[2], int(match[3])
        if op == '+':   result = a + b
        elif op == '-': result = a - b
        elif op == '*': result = a * b
        elif op == '/': result = a / b if b != 0 else 0
        else:           result = 0
        result_str = (
            str(int(result)) if result == int(result)
            else f"{math.floor(result * 100 + 0.5) / 100:.2f}".rstrip("0").rstrip(".")
        )
        log.info(f"数学题: {a} {op} {b} = {result_str}")
        ans_el = await tab.find(placeholder="请输入答案", timeout=5)
        await ans_el.click()
        await ans_el.type_text(result_str, humanize=True)
        ver_btn = await tab.find(tag_name="button", text="验证答案", timeout=5)
        await ver_btn.click()
        await asyncio.sleep(1)

    for _ in range(2):
        try:
            ok = await tab.find(tag_name="button", text="确定", timeout=2)
            await ok.click()
            await asyncio.sleep(0.5)
        except:
            break

    log.info("签到完成")
    await take_screenshot(browser, tab, "03_sign_complete")
    text = await get_text(tab)
    bal = re.search(r'账户余额剩余\s*([\d.]+)\s*积分', text)
    return bal.group(1) if bal else None

# ---------- 续费 ----------
async def renew(browser, tab):
    log.info("检查续费...")
    await tab.go_to(USER_CENTER)
    await asyncio.sleep(2)
    text = await get_text(tab)
    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if not match:
        log.info("未找到到期日")
        return False, None, None
    expiry_str = match.group(1)
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
    remain = (expiry - datetime.now()).days
    log.info(f"到期: {expiry_str}，剩余 {remain} 天")
    if remain > 1:
        log.info("暂不续费")
        return False, expiry_str, remain

    try:
        renew_btn = await tab.find(tag_name="button", text="续费", timeout=5)
        await renew_btn.click()
        await asyncio.sleep(1)
        await tab.find(tag_name="button", text="立即续费", timeout=5).click()
        await asyncio.sleep(1)
        await tab.find(tag_name="button", text="立即支付", timeout=5).click()
        await asyncio.sleep(1)
        ok = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok.click()
        log.info("✅ 续费完成")
        await take_screenshot(browser, tab, "04_renew_complete")
        return True, expiry_str, remain
    except:
        log.warning("续费流程异常")
        return False, expiry_str, remain

# ---------- 主流程 ----------
async def main():
    browser, tab = await create_browser()
    try:
        if not await login(browser, tab):
            wxpush("❌ 登录失败，请检查账号密码或网络")
            return

        balance = await sign(browser, tab)
        renewed, expiry_str, remain = await renew(browser, tab)

        if balance is None and expiry_str:
            await tab.go_to(USER_CENTER)
            await asyncio.sleep(2)
            text = await get_text(tab)
            bal = re.search(r'账户余额剩余\s*([\d.]+)\s*积分', text)
            if bal:
                balance = bal.group(1)

        lines = ["✅ 签到成功"]
        if balance is not None:
            lines.append(f"账户余额剩余 {balance} 积分")
        if expiry_str:
            lines.append(f"到期时间 {expiry_str}")
            if renewed:
                lines.append("✅ 已自动续期")
            else:
                renew_date = (
                    datetime.strptime(expiry_str, "%Y-%m-%d") - timedelta(days=1)
                ).strftime("%Y-%m-%d")
                lines.append(f"不用续期，等到 {renew_date} 再续期")
        wxpush("\n".join(lines))

    except Exception as e:
        log.exception(e)
        await take_screenshot(browser, tab, "99_error")
        wxpush(f"❌ Runfreecloud 任务异常: {e}")
    finally:
        await asyncio.sleep(5)
        await browser.__aexit__(None, None, None)
        log.info("任务结束")

if __name__ == "__main__":
    asyncio.run(main())
