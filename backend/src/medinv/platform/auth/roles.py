ROLE_PERMISSIONS = {
    "OWNER": frozenset(
        {
            "tenant.read",
            "members.read",
            "members.manage",
            "inventory.read",
            "inventory.reserve",
            "inventory.release",
        }
    ),
    "ADMIN": frozenset(
        {
            "tenant.read",
            "members.read",
            "members.manage",
            "inventory.read",
            "inventory.reserve",
            "inventory.release",
        }
    ),
    "OPERATOR": frozenset(
        {"tenant.read", "inventory.read", "inventory.reserve", "inventory.release"}
    ),
    "VIEWER": frozenset({"tenant.read", "inventory.read"}),
}
ROLE_NAMES = {
    "OWNER": "企业所有者",
    "ADMIN": "企业管理员",
    "OPERATOR": "业务操作员",
    "VIEWER": "只读成员",
}


CATALOG_READ = {"catalog.read", "purchase.read"}
OPERATIONS = CATALOG_READ | {"catalog.write", "purchase.write", "purchase.receive"}
QUALITY = CATALOG_READ | {"catalog.review", "purchase.approve", "purchase.inspect", "purchase.post"}
for code in ("OWNER", "ADMIN"):
    ROLE_PERMISSIONS[code] = ROLE_PERMISSIONS[code] | OPERATIONS | QUALITY
ROLE_PERMISSIONS["OPERATOR"] = ROLE_PERMISSIONS["OPERATOR"] | OPERATIONS
ROLE_PERMISSIONS["VIEWER"] = ROLE_PERMISSIONS["VIEWER"] | CATALOG_READ
ROLE_PERMISSIONS["QUALITY"] = frozenset(QUALITY | {"tenant.read", "inventory.read"})
ROLE_NAMES["QUALITY"] = "质量审核员"

for code in ("OWNER", "ADMIN", "OPERATOR"):
    ROLE_PERMISSIONS[code] |= {"opening.write"}
for code in ("OWNER", "ADMIN", "QUALITY"):
    ROLE_PERMISSIONS[code] |= {"opening.review", "opening.post"}

SALES_READ = {"sales.read"}
SALES_OPERATIONS = SALES_READ | {"sales.write", "sales.allocate", "sales.pick"}
SALES_QUALITY = SALES_READ | {"sales.approve", "sales.verify", "sales.post"}
for code in ("OWNER", "ADMIN"):
    ROLE_PERMISSIONS[code] |= SALES_OPERATIONS | SALES_QUALITY | {"sales.credit"}
ROLE_PERMISSIONS["OPERATOR"] |= SALES_OPERATIONS
ROLE_PERMISSIONS["QUALITY"] |= SALES_QUALITY
ROLE_PERMISSIONS["VIEWER"] |= SALES_READ

FINANCE_READ = {"finance.read"}
FINANCE_WRITE = FINANCE_READ | {"finance.write", "finance.allocate", "finance.reverse"}
for code in ("OWNER", "ADMIN"):
    ROLE_PERMISSIONS[code] |= FINANCE_WRITE | {"finance.manage"}
for code in ("OPERATOR", "QUALITY", "VIEWER"):
    ROLE_PERMISSIONS[code] |= FINANCE_READ
ROLE_PERMISSIONS["FINANCE"] = frozenset(
    FINANCE_WRITE | CATALOG_READ | SALES_READ | {"tenant.read", "inventory.read"}
)
ROLE_NAMES["FINANCE"] = "财务人员"

for code in ROLE_PERMISSIONS:
    ROLE_PERMISSIONS[code] |= {"returns.read"}
for code in ("OWNER", "ADMIN", "OPERATOR"):
    ROLE_PERMISSIONS[code] |= {"returns.write"}
for code in ("OWNER", "ADMIN", "QUALITY"):
    ROLE_PERMISSIONS[code] |= {"returns.review", "returns.post"}

for code in ROLE_PERMISSIONS:
    ROLE_PERMISSIONS[code] |= {"retail.read"}
for code in ("OWNER", "ADMIN", "OPERATOR"):
    ROLE_PERMISSIONS[code] |= {"retail.sell"}
for code in ("OWNER", "ADMIN", "FINANCE"):
    ROLE_PERMISSIONS[code] |= {"retail.refund"}

for code in ROLE_PERMISSIONS:
    ROLE_PERMISSIONS[code] |= {"counts.read"}
for code in ("OWNER", "ADMIN", "OPERATOR"):
    ROLE_PERMISSIONS[code] |= {"counts.write"}
for code in ("OWNER", "ADMIN", "QUALITY"):
    ROLE_PERMISSIONS[code] |= {"counts.review", "counts.post"}

for code in ROLE_PERMISSIONS:
    ROLE_PERMISSIONS[code] |= {"transfers.read"}
for code in ("OWNER", "ADMIN", "OPERATOR"):
    ROLE_PERMISSIONS[code] |= {"transfers.write", "transfers.dispatch", "transfers.receive"}
for code in ("OWNER", "ADMIN", "QUALITY"):
    ROLE_PERMISSIONS[code] |= {"transfers.review"}
for code in ("OWNER", "ADMIN"):
    ROLE_PERMISSIONS[code] |= {"warehouses.write"}

for code in ROLE_PERMISSIONS:
    ROLE_PERMISSIONS[code] |= {"quality.read"}
for code in ("OWNER", "ADMIN", "OPERATOR"):
    ROLE_PERMISSIONS[code] |= {"quality.write"}
for code in ("OWNER", "ADMIN", "QUALITY"):
    ROLE_PERMISSIONS[code] |= {"quality.review", "quality.post"}

for code in ("OWNER", "ADMIN", "OPERATOR"):
    ROLE_PERMISSIONS[code] |= {"transfers.loss.write"}
for code in ("OWNER", "ADMIN", "QUALITY"):
    ROLE_PERMISSIONS[code] |= {"transfers.loss.review", "transfers.loss.post"}

# Job roles are explicit compositions of existing permissions. Keep them after
# legacy role expansion so a new operational role does not inherit unrelated work.
JOB_READ = {"tenant.read", "inventory.read", "catalog.read"}
ROLE_PERMISSIONS.update(
    {
        "PURCHASER": frozenset(
            JOB_READ | {"catalog.write", "purchase.read", "purchase.write", "sales.read"}
        ),
        "WAREHOUSE": frozenset(
            JOB_READ
            | {
                "purchase.read",
                "purchase.receive",
                "sales.read",
                "sales.pick",
                "opening.write",
                "counts.read",
                "counts.write",
                "transfers.read",
                "transfers.write",
                "transfers.dispatch",
                "transfers.receive",
                "transfers.loss.write",
                "quality.read",
                "quality.write",
            }
        ),
        "SALES": frozenset(
            JOB_READ | {"sales.read", "sales.write", "sales.allocate", "purchase.read"}
        ),
        "CASHIER": frozenset(JOB_READ | {"retail.read", "retail.sell"}),
    }
)
ROLE_NAMES.update(
    {"PURCHASER": "采购员", "WAREHOUSE": "仓储员", "SALES": "销售员", "CASHIER": "收银员"}
)

for code in ("OWNER", "ADMIN"):
    ROLE_PERMISSIONS[code] |= {"catalog.configure"}

# Legacy returns.write retains its full operational scope. Job roles receive only
# their application kind, or physical custody; review/post/refund remain separate.
for role, permission in {
    "PURCHASER": "returns.purchase.write",
    "SALES": "returns.sales.write",
    "CASHIER": "returns.retail.write",
    "WAREHOUSE": "returns.custody",
}.items():
    ROLE_PERMISSIONS[role] |= {"returns.read", permission}
