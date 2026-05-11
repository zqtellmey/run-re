import asyncio, os, re, time, logging, random, base64, traceback
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

# 启用 beta 模式，提高数字识别率
ocr = ddddocr.DdddOcr(beta=True, show_ad=False)

# ---------- CDP 截图 ----------
async def take_screenshot(browser, tab, name):
    try:
        conn = getattr(browser, '_connection', None) or getattr(browser, 'connection', None)
        if not conn:
            return
        result = await conn.execute("Page.captureScreenshot", {"format": "png"})
        data = result.get("data", "")
        if data:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = SCREENSHOT_DIR / f"{ts}_{name}.png"
            Path(path).write_bytes(base64.b64decode(data))
            log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败: {e}")

async def get_text(tab):
    try:
        return str(await tab.execute_script("return document.body.innerText"))
    except:
        return ""

async def human_delay(min_s=0.3, max_s=0.8):
    await asyncio.sleep(random.uniform(min_s, max_s))

# ---------- 浏览器启动 ----------
async def create_browser():
    options = ChromiumOptions()
    options.headless = False
    options.add_argument("--window-size=1280,720")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--password-store=basic")
    options.add_argument("--use-mock-keychain")
    options.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    options.browser_preferences = {
        "credentials_enable_service": False,
        "profile": {"password_manager_enabled": False},
    }
    browser = await Chrome(options=options).__aenter__()
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
    """返回验证码字符串，失败返回空"""
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
                # JS 注入验证码
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

# ---------- 登录（支持重试）----------
async def login(browser, tab, max_retries=3):
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

        # 处理邮箱输入框：确保为空再填入
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
            # 清空已有内容（清除旧账号）
            await email_el.type_text("", humanize=False)
            await email_el.type_text(EMAIL, humanize=True)
        else:
            log.warning("未找到邮箱输入框")
            continue

        await human_delay()

        # 处理密码输入框
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
            # 清空已有内容
            await pass_el.type_text("", humanize=False)
            await pass_el.type_text(PASSWORD, humanize=True)
        else:
            log.warning("未找到密码输入框")
            continue

        # 获取验证码并填入
        captcha_code = await fill_captcha(tab)
        if not captcha_code:
            log.warning("未能获取验证码，刷新重试")
            continue

        # 点击登录按钮
        try:
            login_btn = await tab.find(css="button.btn.btn-primary", timeout=10)
        except:
            login_btn = await tab.find(tag_name="button", text="登录", timeout=10)
        await login_btn.click()
        await asyncio.sleep(5)

        url = await tab.execute_script("return window.location.href")
        if "/clientarea" in url:
            log.info("✅ 登录成功")
            await take_screenshot(browser, tab, "02_login_success")
            return True

        log.warning(f"登录失败，当前 URL: {url}")
        # 刷新页面，准备下一次重试
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
        return

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
        result_str = str(int(result)) if result == int(result) else f"{result:.2f}"
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
    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if not match:
        log.info("未找到到期日，跳过续费")
        return
    expiry = datetime.strptime(match.group(1), "%Y-%m-%d")
    remain = (expiry - datetime.now()).days
    log.info(f"到期: {match.group(1)}，剩余 {remain} 天")
    if remain > 1:
        log.info("暂不续费")
        return

    try:
        renew_btn = await tab.find(tag_name="button", text="续费", timeout=10)
        await renew_btn.click()
    except:
        return
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

# ---------- 主流程 ----------
async def main():
    browser, tab = await create_browser()
    try:
        if not await login(browser, tab, max_retries=3):
            log.error("登录失败，终止任务")
            return
        await sign(browser, tab)
        await renew(browser, tab)
    except Exception as e:
        log.error(f"任务失败: {e}")
        traceback.print_exc()
        await take_screenshot(browser, tab, "99_error")
    finally:
        await asyncio.sleep(5)
        await browser.__aexit__(None, None, None)
        log.info("任务结束")

if __name__ == "__main__":
    asyncio.run(main())
