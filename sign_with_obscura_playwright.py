import os
import re
import time
import logging
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import ddddocr

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
if not EMAIL or not PASSWORD:
    raise RuntimeError("请在仓库 Secrets 中设置 EMAIL 和 PASSWORD")

BASE_URL = "https://run.freecloud.ltd"
LOGIN_URL = f"{BASE_URL}/login"
USER_CENTER = f"{BASE_URL}/clientarea"
SIGN_PAGE = f"{BASE_URL}/addons?_plugin=5&controller=index&action=index"

SCREENSHOT_DIR = "screenshots"
if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

ocr = ddddocr.DdddOcr()

def save_screenshot(page, name):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{SCREENSHOT_DIR}/{timestamp}_{name}.png"
    page.screenshot(path=filename)
    logging.info(f"📸 截图已保存: {filename}")

def recognize_captcha(page):
    """识别登录页图形验证码（base64）"""
    try:
        page.wait_for_selector("img[src*='base64']", timeout=10)
        img_element = page.query_selector("img[src*='base64']")
        img_src = img_element.get_attribute("src")
        if img_src and img_src.startswith("data:image"):
            import base64 as b64
            base64_str = img_src.split(",")[1]
            img_bytes = b64.b64decode(base64_str)
            return ocr.classification(img_bytes)
    except Exception as e:
        logging.warning(f"base64识别失败: {e}，尝试截图方式")
        try:
            captcha_img = page.query_selector("img[src*='captcha']")
            captcha_img.screenshot(path="captcha.png")
            with open("captcha.png", "rb") as f:
                img_bytes = f.read()
            os.remove("captcha.png")
            return ocr.classification(img_bytes)
        except:
            pass
    return ""

def login(page):
    logging.info("正在访问登录页面...")
    page.goto(LOGIN_URL)
    page.wait_for_selector("input[name='email']", timeout=10)
    page.fill("input[name='email']", EMAIL)
    page.fill("input[name='password']", PASSWORD)
    captcha_text = recognize_captcha(page)
    logging.info(f"验证码识别结果: {captcha_text}")
    captcha_input = page.query_selector("input[name='captcha']")
    captcha_input.click()
    captcha_input.fill(captcha_text)
    page.click("button:has-text('登录')")
    page.wait_for_url("**/clientarea**", timeout=10)
    logging.info("✅ 登录成功")
    save_screenshot(page, "01_login_success")

def handle_math_popup(page):
    try:
        page.wait_for_selector("div.modal:has-text('请计算')", timeout=10)
        popup = page.query_selector("div.modal:has-text('请计算')")
        text = popup.inner_text()
        match = re.search(r'请计算[：:]\s*(\d+)\s*([\+\-\*/])\s*(\d+)', text)
        if not match:
            raise Exception("未提取到数学表达式")
        a = int(match.group(1))
        op = match.group(2)
        b = int(match.group(3))
        result = a + b if op == '+' else a - b if op == '-' else a * b if op == '*' else a // b
        answer = str(result)
        answer_input = popup.query_selector("input[placeholder='请输入答案']")
        answer_input.click()
        answer_input.fill(answer)
        popup.query_selector("button:has-text('验证答案')").click()
        time.sleep(1)
    except Exception as e:
        logging.warning(f"数学弹窗处理异常: {e}")

    # 确认“验证成功”
    try:
        page.wait_for_selector("div.modal:has-text('验证成功')", timeout=5)
        page.click("div.modal button:has-text('确定')")
        logging.info("验证成功弹窗已确认")
    except:
        pass

    # 确认“今天你已经签到过了”
    try:
        page.wait_for_selector("div.modal:has-text('今天你已经签到过了')", timeout=3)
        page.click("div.modal button:has-text('确定')")
        logging.info("今日已签到（已确认）")
    except:
        pass

def sign(page):
    page.goto(SIGN_PAGE)
    page.wait_for_selector("button:has-text('我要签到')", timeout=10)
    page.click("button:has-text('我要签到')")
    handle_math_popup(page)
    logging.info("签到流程完成")
    save_screenshot(page, "02_sign_complete")

def get_product_expiry(page):
    page.goto(USER_CENTER)
    page.wait_for_selector("table tbody tr:first-child", timeout=10)
    rows = page.query_selector_all("table tbody tr")
    for row in rows:
        cols = row.query_selector_all("td")
        if len(cols) < 6:
            continue
        status = cols[0].inner_text().strip()
        if status != '已激活':
            continue
        expiry_str = cols[4].inner_text().strip()
        try:
            return datetime.strptime(expiry_str, "%Y-%m-%d %H:%M")
        except:
            return datetime.strptime(expiry_str[:10], "%Y-%m-%d")
    return None

def renew_product(page):
    expiry = get_product_expiry(page)
    if not expiry:
        logging.info("没有已激活的产品，跳过续费")
        return
    now = datetime.now()
    delta = expiry - now
    logging.info(f"到期时间: {expiry}, 剩余: {delta}")
    if delta > timedelta(days=1):
        logging.info("未接近到期，暂不续费")
        return

    page.goto(USER_CENTER)
    page.wait_for_selector("button:has-text('续费')", timeout=10)
    page.click("button:has-text('续费')")
    page.wait_for_selector("button:has-text('立即续费')", timeout=10)
    page.click("button:has-text('立即续费')")
    page.wait_for_selector("button:has-text('立即支付')", timeout=10)
    page.click("button:has-text('立即支付')")

    try:
        page.wait_for_selector("div.modal button:has-text('确定')", timeout=5)
        page.click("div.modal button:has-text('确定')")
    except:
        pass
    logging.info("续费操作已完成")
    save_screenshot(page, "03_renew_complete")

def main():
    logging.info("===== 开始自动签到(Obscura + Playwright) =====")
    # 通过 playwright 连接到已启动的 Obscura CDP 服务
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        # 获取默认的上下文和页面（Obscura 已自动创建）
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        
        try:
            login(page)
            sign(page)
            renew_product(page)
        except Exception as e:
            logging.error(f"任务执行失败: {e}")
            save_screenshot(page, "99_error")
        finally:
            browser.close()
    logging.info("===== 任务结束 =====")

if __name__ == "__main__":
    main()
