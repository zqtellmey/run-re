import subprocess, sys
try:
    import undetected_chromedriver as uc
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "undetected-chromedriver"])
    import undetected_chromedriver as uc

import os
import re
import time
import logging
import traceback
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
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

def save_screenshot(driver, name):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{SCREENSHOT_DIR}/{timestamp}_{name}.png"
    driver.save_screenshot(filename)
    logging.info(f"📸 截图已保存: {filename}")

def get_driver():
    options = uc.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument('--proxy-server=socks5://127.0.0.1:10808')
    driver = uc.Chrome(options=options)
    return driver

# 下面的 login, sign, renew 等函数就直接复制你上一版本的完整内容，这里省略...

# ---- 以下函数完全不变，直接从之前的脚本复制过来即可 ----
def recognize_captcha(driver):
    try:
        img_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "img[src*='base64']"))
        )
        img_src = img_element.get_attribute("src")
        if img_src.startswith("data:image"):
            import base64
            base64_str = img_src.split(",")[1]
            img_bytes = base64.b64decode(base64_str)
            return ocr.classification(img_bytes)
    except Exception as e:
        logging.warning(f"base64识别失败: {e}，尝试截图方式")
        captcha_img = driver.find_element(By.CSS_SELECTOR, "img[src*='captcha']")
        captcha_img.screenshot("captcha.png")
        with open("captcha.png", "rb") as f:
            img_bytes = f.read()
        os.remove("captcha.png")
        return ocr.classification(img_bytes)
    return ""

def login(driver):
    logging.info("正在访问登录页面...")
    driver.get(LOGIN_URL)
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email")))
    driver.find_element(By.NAME, "email").send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    captcha_text = recognize_captcha(driver)
    logging.info(f"验证码识别结果: {captcha_text}")
    captcha_input = driver.find_element(By.NAME, "captcha")
    captcha_input.clear()
    captcha_input.send_keys(captcha_text)
    login_btn = driver.find_element(By.XPATH, "//button[contains(text(),'登录')]")
    login_btn.click()
    WebDriverWait(driver, 10).until(EC.url_contains("/clientarea"))
    logging.info("✅ 登录成功")
    save_screenshot(driver, "01_login_success")

def handle_math_popup(driver):
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'modal') and contains(.,'请计算')]"))
        )
        popup = driver.find_element(By.XPATH, "//div[contains(@class,'modal') and contains(.,'请计算')]")
        text = popup.text
        match = re.search(r'请计算[：:]\s*(\d+)\s*([\+\-\*/])\s*(\d+)', text)
        if not match:
            raise Exception("未提取到数学表达式")
        a = int(match.group(1))
        op = match.group(2)
        b = int(match.group(3))
        result = a + b if op == '+' else a - b if op == '-' else a * b if op == '*' else a / b
        result = int(result) if result == int(result) else result
        answer_input = popup.find_element(By.XPATH, ".//input[@placeholder='请输入答案']")
        answer_input.clear()
        answer_input.send_keys(str(result))
        popup.find_element(By.XPATH, ".//button[contains(text(),'验证答案')]").click()
        time.sleep(1)
    except Exception as e:
        logging.warning(f"数学弹窗处理异常: {e}")

    # 确认“验证成功”
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'modal') and contains(.,'验证成功')]"))
        )
        driver.find_element(By.XPATH, "//div[contains(@class,'modal')]//button[contains(text(),'确定')]").click()
        logging.info("验证成功弹窗已确认")
    except:
        pass

    # 确认“今天你已经签到过了”
    try:
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'modal') and contains(.,'今天你已经签到过了')]"))
        )
        driver.find_element(By.XPATH, "//div[contains(@class,'modal')]//button[contains(text(),'确定')]").click()
        logging.info("今日已签到（已确认）")
    except:
        pass

def sign(driver):
    driver.get(SIGN_PAGE)
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//button[contains(text(),'我要签到')]")))
    sign_btn = driver.find_element(By.XPATH, "//button[contains(text(),'我要签到')]")
    sign_btn.click()
    handle_math_popup(driver)
    logging.info("签到流程完成")
    save_screenshot(driver, "02_sign_complete")

def get_product_expiry(driver):
    driver.get(USER_CENTER)
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//table//tbody/tr[1]")))
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if len(cols) < 6:
            continue
        status = cols[0].text.strip()
        if status != '已激活':
            continue
        expiry_str = cols[4].text.strip()
        try:
            return datetime.strptime(expiry_str, "%Y-%m-%d %H:%M")
        except:
            return datetime.strptime(expiry_str[:10], "%Y-%m-%d")
    return None

def renew_product(driver):
    expiry = get_product_expiry(driver)
    if not expiry:
        logging.info("没有已激活的产品，跳过续费")
        return
    now = datetime.now()
    delta = expiry - now
    logging.info(f"到期时间: {expiry}, 剩余: {delta}")
    if delta > timedelta(days=1):
        logging.info("未接近到期，暂不续费")
        return

    driver.get(USER_CENTER)
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//button[contains(text(),'续费')]")))
    driver.find_element(By.XPATH, "//button[contains(text(),'续费')]").click()

    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//button[contains(text(),'立即续费')]")))
    driver.find_element(By.XPATH, "//button[contains(text(),'立即续费')]").click()

    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//button[contains(text(),'立即支付')]")))
    driver.find_element(By.XPATH, "//button[contains(text(),'立即支付')]").click()

    try:
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'modal')]//button[contains(text(),'确定')]")))
        driver.find_element(By.XPATH, "//div[contains(@class,'modal')]//button[contains(text(),'确定')]").click()
    except:
        pass
    logging.info("续费操作已完成")
    save_screenshot(driver, "03_renew_complete")

def main():
    logging.info("===== 开始自动签到(V2Ray 代理 + undetected) =====")
    driver = get_driver()
    try:
        login(driver)
        sign(driver)
        renew_product(driver)
    except Exception as e:
        logging.error(f"任务执行失败: {e}")
        logging.error(traceback.format_exc())
        save_screenshot(driver, "99_error")
    finally:
        driver.quit()
    logging.info("===== 任务结束 =====")

if __name__ == "__main__":
    main()
