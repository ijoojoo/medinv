export interface NavigationItem {
  page: string;
  name: string;
  icon: string;
  permission: string;
}
export interface NavigationGroup {
  id: string;
  name: string;
  items: NavigationItem[];
}

// Empty categories are reserved for future modules and are hidden by the renderer.
export const navigationGroups: NavigationGroup[] = [
  {
    id: "purchase",
    name: "采购",
    items: [
      {
        page: "purchasing",
        name: "采购入库",
        icon: "⇣",
        permission: "purchase.read",
      },
    ],
  },
  {
    id: "sales",
    name: "销售",
    items: [
      {
        page: "selling",
        name: "批发销售",
        icon: "⇡",
        permission: "sales.read",
      },
      {
        page: "retail",
        name: "零售收银",
        icon: "▣",
        permission: "retail.read",
      },
    ],
  },
  {
    id: "inventory",
    name: "库存",
    items: [
      {
        page: "openings",
        name: "期初库存",
        icon: "⊕",
        permission: "inventory.read",
      },
      {
        page: "inventory",
        name: "库存查询",
        icon: "▤",
        permission: "inventory.read",
      },
      {
        page: "transfers",
        name: "仓库调拨",
        icon: "⇆",
        permission: "transfers.read",
      },
      {
        page: "location-moves",
        name: "同仓移库",
        icon: "↔",
        permission: "transfers.read",
      },
      {
        page: "counts",
        name: "库存盘点",
        icon: "☷",
        permission: "counts.read",
      },
      {
        page: "ledger",
        name: "库存台账",
        icon: "≡",
        permission: "inventory.read",
      },
    ],
  },
  {
    id: "quality",
    name: "质量",
    items: [
      {
        page: "admission",
        name: "经营范围与准入",
        icon: "◎",
        permission: "catalog.read",
      },
      {
        page: "quality",
        name: "质量处置",
        icon: "◇",
        permission: "quality.read",
      },
    ],
  },
  {
    id: "aftersales",
    name: "售后",
    items: [
      {
        page: "returns",
        name: "退货与退款",
        icon: "↩",
        permission: "returns.read",
      },
    ],
  },
  {
    id: "data",
    name: "资料",
    items: [
      {
        page: "dictionaries",
        name: "商品字典",
        icon: "▤",
        permission: "catalog.read",
      },
      {
        page: "products",
        name: "商品档案",
        icon: "▧",
        permission: "catalog.read",
      },
      {
        page: "parties",
        name: "往来单位",
        icon: "♙",
        permission: "catalog.read",
      },
      {
        page: "warehouses",
        name: "仓库与库位",
        icon: "⌂",
        permission: "inventory.read",
      },
    ],
  },
  {
    id: "finance",
    name: "财务",
    items: [
      {
        page: "receivables",
        name: "销售应收",
        icon: "￥",
        permission: "sales.read",
      },
      {
        page: "finance",
        name: "往来结算",
        icon: "⇄",
        permission: "finance.read",
      },
    ],
  },
  { id: "operations", name: "运营", items: [] },
  { id: "insurance", name: "医保", items: [] },
  {
    id: "settings",
    name: "设置",
    items: [
      { page: "appearance", name: "界面与操作", icon: "◧", permission: "" },
      {
        page: "productFields",
        name: "商品字段设置",
        icon: "▤",
        permission: "catalog.configure",
      },
      {
        page: "members",
        name: "成员与权限",
        icon: "♧",
        permission: "members.read",
      },
    ],
  },
];

export const groupForPage = (page: string) =>
  navigationGroups.find((group) =>
    group.items.some((item) => item.page === page),
  );
export const itemForPage = (page: string) =>
  groupForPage(page)?.items.find((item) => item.page === page);
