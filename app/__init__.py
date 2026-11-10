"""券商研報平台的應用程式套件（框架核心；機構專屬的設定在 `profiles/<名稱>/`，見 `app/profile.py`）。"""

# 框架本身的名稱：對外可見、指稱「這個框架」的地方（FastAPI 的 title 等）一律引用它，不寫字面值。
# 機構的產品名是 profile 的 `[institution].product_name`，兩者不可混用。
FRAMEWORK_NAME = "券商研報平台"
