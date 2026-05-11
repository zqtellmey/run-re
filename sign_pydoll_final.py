import asyncio, os, re, time, logging, random, base64, traceback, json, math
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

# ---------- Cookie 缓存 ----------
COOKIE_FILE = Path("./cookies.json")

def load_cookies():
    if COOKIE_FILE.exists():
        try:
            with open(COOKIE_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_cookies(cookies):
    with open(COOKIE_FILE, 'w') as f:
        json.dump(cookies, f)

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

# ---------- 截图（使用 pydoll 内置方法 + CDP 备用）----------
async def take_screenshot(browser, tab, name):
    """使用 tab.take_screenshot() 或 execute_cdp_command 作为备用"""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{ts}_{name}.png")
        await tab.take_screenshot(path=path)
        log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败(take_screenshot): {e}")
        try:
            # 备用方案：CDP 命令
            result = await tab.execute_cdp_command(
                "Page.captureScreenshot",
                {"format": "png"}
            )
            data = result.get("data", "")
            if data:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = SCREENSHOT_DIR / f"{ts}_{name}.png"
                Path(path).write_bytes(base64.b64decode(data))
                log.info(f"📸 截图(CDP备用): {path}")
        except Exception as e2:
            log.warning(f"截图所有方案失败: {e2}")

async def get_text(tab):
    try:
        result = await tab.execute_script("return document.body.innerText")
        if isinstance(result, dict):
            return result.get("result", {}).get("result", {}).get("value", "")
        return str(result)
    except:
        return ""

async def human_delay(min_s=0.3, max_s=0.8):
    await asyncio.sleep(random.uniform(min_s, max_s))

# ---------- 探测 Chromium 路径 ----------
def _find_chromium() -> str | None:
    candidates = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/snap/bin/chromium",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            log.info(f"找到 Chromium: {p}")
            return p
    try:
        import subprocess
        result = subprocess.run(
            ["which", "chromium-browser", "chromium", "google-chrome"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line and os.path.isfile(line):
                log.info(f"which 找到: {line}")
                return line
    except Exception:
        pass
    return None

# ---------- 浏览器启动 ----------
async def create_browser():
    opts = ChromiumOptions()
    opts.headless = False
    path = _find_chromium()
    if path:
        opts.binary_location = path
    else:
        log.warning("未找到 Chromium，使用 pydoll 默认路径")

    opts.add_argument("--window-size=1280,720")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-save-password-bubble")
    opts.add_argument("--disable-password-generation")
    opts.add_argument("--password-store=basic")
    opts.add_argument("--use-mock-keychain")
    # 代理
    opts.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    opts.browser_preferences = {
        "credentials_enable_service": False,
        "profile": {
            "password_manager_enabled": False,
            "default_content_setting_values": {
                "notifications": 2,
                "geolocation": 2,
            },
        },
    }
    browser = await Chrome(options=opts).__aenter__()
    tab = await browser.start()
    return browser, tab

# ---------- Cloudflare 手动点击 ----------
async def manual_cf_click(tab, timeout=15):
    log.info("尝试手动完成 Cloudflare 验证...")
    for i in range(timeout):
        body = await get_text(tab)
        if "email" in body or "登录" in body:
            log.info("验证已完成")
            return True
        await tab.execute_script("""
            (function() {
                const checkboxes = document.querySelectorAll('iframe');
                for (let iframe of checkboxes) {
                    try {
                        const doc = iframe.contentDocument || iframe.contentWindow.document;
                        const cb = doc.querySelector('#checkbox, input[type="checkbox"]');
                        if (cb) {
                            cb.click();
                            cb.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            cb.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    } catch(e) {}
                }
            })()
        """)
        await asyncio.sleep(1)
    return False

# ---------- 获取并填入验证码 ----------
async def fill_captcha(tab):
    for _ in range(3):
        try:
            cap_img = await tab.find(id="allow_login_email_captcha", timeout=5)
        except:
            cap_img = None
        if not cap_img:
            try:
                cap_img = await tab.find(tag_name="img", alt="验证码", timeout=5)
            except:
                cap_img = None
        if cap_img:
            src = cap_img.get_attribute("src")
            if src and src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                raw = ocr.classification(img_bytes)
                code = re.sub(r'[^0-9]', '', raw)
                log.info(f"验证码识别: {raw} -> {code}")
                await tab.execute_script(f"""
                    (function() {{
                        var input = document.querySelector('#captcha_allow_login_email_captcha') ||
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

# ---------- Cookie 恢复登录 ----------
async def try_restore_cookies(tab):
    cookies = load_cookies()
    if not cookies:
        return False

    log.info(f"尝试恢复 {len(cookies)} 个 Cookie...")
    await tab.go_to(BASE_URL)
    await asyncio.sleep(2)

    for c in cookies:
        try:
            await tab.set_cookie(
                name=c["name"],
                value=c["value"],
                domain=c.get("domain", "run.freecloud.ltd"),
                path=c.get("path", "/"),
            )
        except Exception as e:
            log.debug(f"Cookie 设置失败 {c.get('name')}: {e}")

    await tab.go_to(USER_CENTER)
    await asyncio.sleep(3)

    try:
        url = await tab.execute_script("return window.location.href")
        if isinstance(url, dict):
            url = url.get("result", {}).get("result", {}).get("value", "")
    except:
        url = ""

    if "/clientarea" in url and "login" not in url:
        log.info("✅ Cookie 有效，恢复登录成功")
        return True

    log.info("Cookie 已失效，需重新登录")
    return False

# ---------- 登录成功后保存 Cookie ----------
async def save_session_cookies(tab):
    try:
        # 方式1：pydoll 原生
        cookies = await tab.get_cookies()
    except:
        cookies = []

    if not cookies:
        try:
            # 方式2：CDP 命令
            result = await tab.execute_cdp_command(
                "Network.getCookies",
                {"urls": [BASE_URL, LOGIN_URL, USER_CENTER]}
            )
            cookies = result.get("cookies", [])
        except Exception as e:
            log.warning(f"获取 Cookie 失败: {e}")
            return

    if cookies:
        cookie_list = [{
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
        } for c in cookies if c.get("name") and c.get("value")]
        save_cookies(cookie_list)
        log.info(f"✅ 已保存 {len(cookie_list)} 个 Cookie")
    else:
        log.warning("未获取到任何 Cookie")

# ---------- 登录 ----------
async def login(browser, tab, max_retries=3):
    # 先尝试 Cookie 恢复
    if await try_restore_cookies(tab):
        return True

    for attempt in range(1, max_retries + 1):
        log.info(f"登录尝试 {attempt}/{max_retries}")
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to(LOGIN_URL)
        except Exception:
            await tab.go_to(LOGIN_URL)

        await asyncio.sleep(3)
        body = await get_text(tab)
        if "verify you are human" in body.lower() or "performing security verification" in body.lower():
            if not await manual_cf_click(tab):
                log.warning("Cloudflare 验证可能未完成")

        # 填写邮箱
        email_el = None
        for selector in [
            {"tag_name": "input", "name": "email"},
            {"placeholder": "请输入邮箱地址"},
            {"placeholder": "请输入您的邮箱"},
        ]:
            try:
                email_el = await tab.find(**selector, timeout=5)
                break
            except:
                continue
        if email_el:
            await email_el.click()
            await email_el.type_text("")  # 清空旧值
            await email_el.type_text(EMAIL, humanize=True)
        else:
            log.warning("未找到邮箱输入框")
            continue

        await human_delay()

        # 填写密码
        pass_el = None
        for selector in [
            {"tag_name": "input", "name": "password"},
            {"placeholder": "请输入登录密码"},
            {"placeholder": "请输入您的密码"},
        ]:
            try:
                pass_el = await tab.find(**selector, timeout=5)
                break
            except:
                continue
        if pass_el:
            await pass_el.click()
            await pass_el.type_text("")  # 清空旧值
            await pass_el.type_text(PASSWORD, humanize=True)
        else:
            log.warning("未找到密码输入框")
            continue

        captcha_code = await fill_captcha(tab)
        if not captcha_code:
            log.warning("未能获取验证码，刷新重试")
            continue

        # 点击登录
        try:
            login_btn = await tab.find(css="button.btn.btn-primary", timeout=10)
        except:
            login_btn = await tab.find(tag_name="button", text="登录", timeout=10)
        await login_btn.click()
        await asyncio.sleep(5)

        # 判断登录结果
        try:
            url = await tab.execute_script("return window.location.href")
            if isinstance(url, dict):
                url = url.get("result", {}).get("result", {}).get("value", "")
        except:
            url = ""
        log.info(f"当前 URL: {url}")
        if "/clientarea" in url:
            log.info("✅ 登录成功")
            await take_screenshot(browser, tab, "02_login_success")
            await save_session_cookies(tab)
            return True

        log.warning(f"登录失败，当前 URL: {url}")
        await asyncio.sleep(1)

    log.error("多次登录尝试均失败")
    return False

# ---------- 签到 ----------
async def sign(browser, tab):
    log.info("前往签到页...")
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(SIGN_PAGE)
    except:
        await tab.go_to(SIGN_PAGE)
    await asyncio.sleep(3)

    try:
        btn = await tab.find(tag_name="button", text="我要签到", timeout=10)
        await btn.click()
    except:
        log.info("可能已经签到过了")
        await take_screenshot(browser, tab, "02_sign_skip")
        return None

    await asyncio.sleep(2)
    text = await get_text(tab)
    match = re.search(r'请计算[：:]\s*(\d+)\s*([+\-*/])\s*(\d+)', text)
    if match:
        a, op, b = int(match[1]), match[2], int(match[3])
        if op == '+': result = a + b
        elif op == '-': result = a - b
        elif op == '*': result = a * b
        elif op == '/': result = a / b if b != 0 else 0
        else: result = 0
        if result == int(result):
            result_str = str(int(result))
        else:
            rounded = math.floor(result * 100 + 0.5) / 100
            result_str = f"{rounded:.2f}".rstrip("0").rstrip(".")
        ans_el = await tab.find(placeholder="请输入答案", timeout=5)
        await ans_el.click()
        await ans_el.type_text(result_str, humanize=True)
        ver_btn = await tab.find(tag_name="button", text="验证答案", timeout=5)
        await ver_btn.click()
        await asyncio.sleep(2)

    for _ in range(2):
        try:
            ok = await tab.find(tag_name="button", text="确定", timeout=3)
            await ok.click()
            await asyncio.sleep(1)
        except:
            break
    log.info("签到完成")
    await take_screenshot(browser, tab, "03_sign_complete")

    text_after = await get_text(tab)
    balance_match = re.search(r'账户余额剩余\s*([\d.]+)\s*积分', text_after)
    if balance_match:
        return balance_match.group(1)
    balance_match2 = re.search(r'(?:积分余额|账户余额)[：:\s]+([\d.]+)\s*积分', text_after)
    if balance_match2:
        return balance_match2.group(1)
    return None

# ---------- 续费 ----------
async def renew(browser, tab):
    log.info("检查续费...")
    try:
        async with tab.expect_and_bypass_cloudflare_captcha():
            await tab.go_to(USER_CENTER)
    except:
        await tab.go_to(USER_CENTER)
    await asyncio.sleep(3)

    text = await get_text(tab)
    clientarea_balance = None
    bal_m = re.search(r'账户余额剩余\s*([\d.]+)\s*积分', text)
    if bal_m:
        clientarea_balance = bal_m.group(1)
    else:
        bal_m2 = re.search(r'([\d.]+)\s*\n?\s*积分[\s\S]{0,30}?可用余额|可用余额[\s\S]{0,30}?([\d.]+)\s*\n?\s*积分', text)
        if bal_m2:
            clientarea_balance = bal_m2.group(1) or bal_m2.group(2)

    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if not match:
        log.info("未找到到期日，跳过续费")
        return None, None, False, clientarea_balance
    expiry_str = match.group(1)
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
    remain = (expiry - datetime.now()).days
    log.info(f"到期: {expiry_str}，剩余 {remain} 天")
    if remain > 1:
        log.info("暂不续费")
        return expiry_str, remain, False, clientarea_balance

    try:
        renew_btn = await tab.find(tag_name="button", text="续费", timeout=10)
        await renew_btn.click()
    except:
        return expiry_str, remain, False, clientarea_balance
    await asyncio.sleep(2)
    try:
        confirm = await tab.find(tag_name="button", text="立即续费", timeout=5)
        await confirm.click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        pay = await tab.find(tag_name="button", text="立即支付", timeout=5)
        await pay.click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        ok = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok.click()
    except:
        pass
    log.info("续费完成")
    await take_screenshot(browser, tab, "04_renew_complete")
    return expiry_str, remain, True, clientarea_balance

# ---------- 主流程 ----------
async def main():
    browser, tab = await create_browser()
    try:
        if not await login(browser, tab):
            wxpush("❌ Runfreecloud 登录失败，请检查账号密码或验证码")
            return

        balance = await sign(browser, tab)
        expiry_str, remain, renewed, clientarea_balance = await renew(browser, tab)

        final_balance = balance if balance is not None else clientarea_balance

        lines = ["✅ 签到成功"]
        if final_balance is not None:
            lines.append(f"账户余额剩余 {final_balance} 积分")
        if expiry_str:
            lines.append(f"到期时间 {expiry_str}")
            if renewed:
                lines.append("已自动续期")
            else:
                from datetime import timedelta
                renew_date = (datetime.strptime(expiry_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                lines.append(f"不用续期 等到 {renew_date} 再续期")
        wxpush("\n".join(lines))

    except Exception as e:
        log.error(f"任务失败: {e}")
        traceback.print_exc()
        await take_screenshot(browser, tab, "99_error")
        wxpush(f"❌ Runfreecloud 任务异常: {e}")
    finally:
        await asyncio.sleep(5)
        await browser.__aexit__(None, None, None)
        log.info("任务结束")

if __name__ == "__main__":
    asyncio.run(main())
