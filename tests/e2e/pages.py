"""Page Objects: one class per screen, so tests read like user steps and a UI
change is fixed in one place, not in every test."""
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

TIMEOUT = 10


class Page:
    path = "/"

    def __init__(self, driver, base_url):
        self.d, self.base = driver, base_url
        self.wait = WebDriverWait(driver, TIMEOUT)

    def open(self, query=""):
        self.d.get(self.base + self.path + query)
        return self

    def el(self, css):
        return self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, css)))

    def text_appears(self, css):
        self.wait.until(lambda d: d.find_element(By.CSS_SELECTOR, css).text.strip() != "")
        return self.d.find_element(By.CSS_SELECTOR, css).text

    def logout(self):
        self.el("#logout").click()
        self.wait.until(EC.url_to_be(self.base + "/"))
        return LoginPage(self.d, self.base)


class LoginPage(Page):
    path = "/"

    def login(self, customer_id, password):
        self.el("#customer-id").clear()
        self.el("#customer-id").send_keys(str(customer_id))
        self.el("#password").clear()
        self.el("#password").send_keys(password)
        self.el("#login-btn").click()
        return self

    def go_to_dashboard(self, customer_id, password):
        self.login(customer_id, password)
        self.wait.until(EC.url_contains("/dashboard"))
        return DashboardPage(self.d, self.base).ready()

    def error(self):
        return self.text_appears("#login-error")


class DashboardPage(Page):
    path = "/dashboard"

    def ready(self):
        self.el(".acct")
        return self

    def accounts(self):
        cards = self.d.find_elements(By.CSS_SELECTOR, ".acct")
        return [{"id": int(c.get_attribute("data-account-id")),
                 "number": c.find_element(By.CSS_SELECTOR, ".number").text,
                 "balance": c.find_element(By.CSS_SELECTOR, ".bal").text} for c in cards]

    def open_statement(self, index=0):
        self.d.find_elements(By.CSS_SELECTOR, ".view-statement")[index].click()
        return StatementPage(self.d, self.base).ready()


class StatementPage(Page):
    path = "/statement"

    def ready(self):
        self.el("tr.txn")
        return self

    def row_ids(self):
        return [int(r.get_attribute("data-id"))
                for r in self.d.find_elements(By.CSS_SELECTOR, "tr.txn")]

    def descriptions(self):
        return [c.text for c in self.d.find_elements(By.CSS_SELECTOR, "tr.txn td.desc")]

    def search(self, text):
        box = self.el("#search")
        box.clear()
        box.send_keys(text)
        first = self.d.find_elements(By.CSS_SELECTOR, "tr.txn")
        self.el("#search-btn").click()
        if first:
            self.wait.until(EC.staleness_of(first[0]))
        self.wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "tr.txn")
                        or d.find_element(By.ID, "empty").is_displayed())
        return self

    def load_more(self):
        n = len(self.row_ids())
        self.el("#load-more").click()
        self.wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "tr.txn")) > n)
        return self


class TransferPage(Page):
    path = "/transfer"

    def ready(self):
        self.wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#from option")) > 0)
        return self

    def fill(self, to_number, amount, description="", from_index=0):
        Select(self.el("#from")).select_by_index(from_index)
        for css, val in (("#to", to_number), ("#amount", amount), ("#description", description)):
            self.el(css).clear()
            self.el(css).send_keys(val)
        return self

    def submit(self):
        self.el("#send-btn").click()
        return self

    def double_submit(self):
        btn = self.el("#send-btn")
        self.d.execute_script("arguments[0].click(); arguments[0].disabled = false; "
                              "arguments[0].click();", btn)
        return self

    def success(self):
        return self.text_appears("#transfer-success")

    def error(self):
        return self.text_appears("#transfer-error")
