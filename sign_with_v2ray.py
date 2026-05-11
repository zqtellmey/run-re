import asyncio
import os
import re
import json
import time
import logging
import base64
from pathlib import Path
from datetime import datetime, timedelta

from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr
import httpx  # 可选，如果需要通知

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
BASE_URL = "https://run.freecloud.ltd"
LOGIN_URL = f"{BASE_URL}/login"
USER_CENTER = f"{BASE_URL}/clientarea"
SIGN_PAGE = f"{BASE_URL}/addons?_plugin=5&controller=index&action=index"
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

# 截图目录
SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)
ocr = ddddocr.DdddOcr(show_ad=False)

# ---------- 工具函数 ----------
async def human_delay(min_s=0.2, max_s=0.8):
    await asyncio.sleep(min_s + random.random() * (max_s - min_s))

async def take_screenshot(tab, name, browser=None):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = SCREENSHOT_DIR / f"{timestamp}_{name}.png"
        await tab.screenshot(str(fname))
        log.info(f"截图: {fname}")
    except Exception:
        pass

async def try_get_text(tab):
    try:
        return str(await tab.execute_script("return document.body.innerText"))
    except:
        return ""

# ---------- 浏览器驱动 ----------
async def create_browser():
    options = ChromiumOptions()
    options.headless = HEADLESS
    options.add_argument("--window-size=1280,720")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-gpu")
    # 代理
    options.add_argument("--proxy-server=socks5://127.0.0.1:10808")
    browser = await Chrome(options=options).__aenter__()
    tab = await browser.start()
    return browser, tab

# ---------- 登录 ----------
async def login(tab):
    log.info("访问登录页，绕过 Cloudflare...")
    async with tab.expect_and_bypass_cloudflare_captcha():
        await tab.go_to(LOGIN_URL)
    await asyncio.sleep(2)
    await take_screenshot(tab, "01_login_page")

    # 填表
    try:
        email_el = await tab.find(tag_name="input", name="email", timeout=10)
        await email_el.click()
        await email_el.type_text(EMAIL, humanize=True)
        await human_delay()
        pass_el = await tab.find(tag_name="input", name="password")
        await pass_el.click()
        await pass_el.type_text(PASSWORD, humanize=True)
    except Exception:
        await tab.execute_script(f"""
            document.querySelector('input[name="email"]').value='{EMAIL}';
            document.querySelector('input[name="password"]').value='{PASSWORD}';
        """)

    # 识别验证码
    for _ in range(3):
        try:
            img = await tab.find(tag_name="img")
            src = await img.get_attribute("src")
            if src and src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                captcha = ocr.classification(img_bytes)
                captcha = re.sub(r'[^a-zA-Z0-9]', '', captcha)
                log.info(f"验证码: {captcha}")
                captcha_el = await tab.find(tag_name="input", name="captcha", timeout=5)
                await captcha_el.click()
                await captcha_el.type_text(captcha, humanize=True)
                break
        except Exception as e:
            log.warning(f"验证码重试: {e}")
            await human_delay(1, 2)

    # 点击登录
    btn = await tab.find(tag_name="button", text="登录", timeout=5)
    await btn.click()
    await asyncio.sleep(3)

    url = await tab.execute_script("return window.location.href")
    if "/clientarea" in url:
        log.info("✅ 登录成功")
        await take_screenshot(tab, "02_login_success")
        return True
    log.error(f"登录失败，当前 URL: {url}")
    return False

# ---------- 签到 ----------
async def sign(tab):
    log.info("开始签到...")
    async with tab.expect_and_bypass_cloudflare_captcha():
        await tab.go_to(SIGN_PAGE)
    await asyncio.sleep(2)

    try:
        sign_btn = await tab.find(tag_name="button", text="我要签到", timeout=10)
        await sign_btn.click()
    except Exception:
        log.info("可能今天已经签到过了")
        return

    # 处理数学弹窗
    try:
        for _ in range(5):
            text = await try_get_text(tab)
            if "请计算" in text:
                break
            await asyncio.sleep(1)
        match = re.search(r'请计算[：:]\s*(\d+)\s*([+\-*/])\s*(\d+)', text)
        if match:
            a, op, b = int(match[1]), match[2], int(match[3])
            result = a + b if op == '+' else a - b if op == '-' else a * b if op == '*' else a // b
            answer = str(result)
            log.info(f"答案: {answer}")
            ans_el = await tab.find(tag_name="input", placeholder="请输入答案", timeout=5)
            await ans_el.click()
            await ans_el.type_text(answer, humanize=True)
            ver_btn = await tab.find(tag_name="button", text="验证答案", timeout=5)
            await ver_btn.click()
            await asyncio.sleep(2)
    except Exception as e:
        log.warning(f"数学弹窗处理异常: {e}")

    # 关闭可能的结果弹窗
    try:
        ok = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok.click()
    except Exception:
        pass
    log.info("签到完成")
    await take_screenshot(tab, "03_sign_complete")

# ---------- 续费 ----------
async def renew(tab):
    log.info("检查续费...")
    async with tab.expect_and_bypass_cloudflare_captcha():
        await tab.go_to(USER_CENTER)
    await asyncio.sleep(2)

    text = await try_get_text(tab)
    # 简单判断到期日
    match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if not match:
        log.info("未找到到期日，跳过续费")
        return
    expiry_str = match.group(1)
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
    now = datetime.now()
    remain = (expiry - now).days
    log.info(f"到期日: {expiry_str}，剩余 {remain} 天")
    if remain > 1:
        log.info("未到续费时间，跳过")
        return

    # 点击续费按钮
    try:
        renew_btn = await tab.find(tag_name="button", text="续费", timeout=10)
        await renew_btn.click()
    except:
        log.warning("找不到续费按钮")
        return

    await asyncio.sleep(2)
    try:
        confirm_btn = await tab.find(tag_name="button", text="立即续费", timeout=5)
        await confirm_btn.click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        pay_btn = await tab.find(tag_name="button", text="立即支付", timeout=5)
        await pay_btn.click()
    except:
        pass
    await asyncio.sleep(2)
    try:
        ok_btn = await tab.find(tag_name="button", text="确定", timeout=3)
        await ok_btn.click()
    except:
        pass
    log.info("续费操作完成")
    await take_screenshot(tab, "04_renew_complete")

# ---------- 主流程 ----------
async def main():
    browser, tab = await create_browser()
    try:
        if not await login(tab):
            return
        await sign(tab)
        await renew(tab)
    except Exception as e:
        log.exception(f"任务异常: {e}")
        await take_screenshot(tab, "99_error")
    finally:
        await browser.__aexit__(None, None, None)
        log.info("任务结束")

if __name__ == "__main__":
    import random
    asyncio.run(main())
