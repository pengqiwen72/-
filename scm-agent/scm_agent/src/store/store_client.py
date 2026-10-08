"""
长期记忆（LangGraph Store）访问封装 —— 供应链域命名空间。

命名空间约定与 `store/seed_loader.py` 的写入侧一一对应
-------------------------------------------------------
    ("operator",)                  key=operator_id
    ("material",)                  key=material_code
    ("supplier",)                  key=supplier_code
    ("warehouse",)                 key=warehouse_code
    ("stock", material_code)       key=warehouse_code   按物料看多仓
    ("stock_wh", warehouse_code)   key=material_code    按仓库盘点
    ("quote", material_code)       key=supplier_code    某物料的各家报价
    ("purchase_order",)            key=po_no
    ("receipt",)                   key=receipt_no
    ("demand",)                    key=demand_id
    ("ticket",)                    key=ticket_no

读者与写者必须是同一套口径：命名空间写错一个字符不会报错，只会「查不到」——
看起来像数据不存在，排查成本很高。所以这里每个方法都紧挨着写清楚它读哪个空间。
"""

from store.store_provider import resolve_store
from utils.debug_aspect import debug
from typing import List, Dict, Any, Optional
from langgraph.store.base import SearchItem, Item, BaseStore

# 「要全部」时用的条数上限。search() 的默认 limit 是 10，而物料主数据有 47 条、
# 库存点有 120 个 —— 不显式传就会静默丢数据。
_SEARCH_ALL = 10000


class StoreClient:
    """
    长期记忆（LangGraph Store）访问封装。

    注意：store 必须**懒加载**——脱离 LangGraph 运行时时 `get_store()` 会抛
    RuntimeError，若在构造时解析会导致模块导入即失败（离线 demo 无法运行）。
    """

    def __init__(self, store: Optional[BaseStore] = None):
        # 允许显式注入（测试用）；默认延迟到首次访问时解析
        self._store = store

    @property
    def store(self) -> BaseStore:
        """首次访问时解析可用的 store（运行时 store 优先，离线回落 InMemoryStore）。"""
        if self._store is None:
            self._store = resolve_store()
        return self._store

    # ==================== 辅助工具 ====================
    def item2dict(self, item: Item) -> Dict[str, Any]:
        if not item:
            return {}
        if isinstance(item.value, dict):
            return item.value.copy()  ## 避免影响原值
        return item.value

    def search_items_to_dict_list(self, searchitems: List[SearchItem]) -> List[Dict[str, Any]]:
        if not searchitems:
            return []
        return [self.item2dict(item) for item in searchitems]


    # ==================== 通用方法 ====================
    @debug
    def get_item(self, namespace: tuple, key: str) -> Dict[str, Any]:
        item = self.store.get(namespace, key)
        return self.item2dict(item)

    @debug
    def put_item(self, namespace: tuple, key: str, value: Dict[str, Any]) -> None:
        self.store.put(namespace, key, value)

    @debug
    def get_all_namespaces(self, limit: int = 100) -> list:
        return self.store.list_namespaces(limit=limit)

    @debug
    def list_namespace(self, namespace: tuple, limit: int = _SEARCH_ALL) -> List[Dict[str, Any]]:
        """某个命名空间下的全部记录（含多段前缀的，如 ("stock", "M100023")）。"""
        return self.search_items_to_dict_list(self.store.search(namespace, limit=limit))

    # ==================== 操作员 ====================
    @debug
    def get_operator_by_id(self, operator_id: str) -> Dict[str, Any]:
        if not operator_id:
            return {}
        return self.get_item(("operator",), operator_id)

    @debug
    def list_all_operators(self) -> List[Dict[str, Any]]:
        return self.list_namespace(("operator",))

    # ==================== 物料主数据 ====================
    @debug
    def get_material(self, material_code: str) -> Dict[str, Any]:
        if not material_code:
            return {}
        return self.get_item(("material",), material_code)

    @debug
    def list_all_materials(self) -> List[Dict[str, Any]]:
        return self.list_namespace(("material",))

    # ==================== 供应商主数据 ====================
    @debug
    def get_supplier(self, supplier_code: str) -> Dict[str, Any]:
        if not supplier_code:
            return {}
        return self.get_item(("supplier",), supplier_code)

    @debug
    def list_all_suppliers(self) -> List[Dict[str, Any]]:
        return self.list_namespace(("supplier",))

    # ==================== 仓库主数据 ====================
    @debug
    def get_warehouse(self, warehouse_code: str) -> Dict[str, Any]:
        if not warehouse_code:
            return {}
        return self.get_item(("warehouse",), warehouse_code)

    @debug
    def list_all_warehouses(self) -> List[Dict[str, Any]]:
        return self.list_namespace(("warehouse",))

    # ==================== 库存（两个视角） ====================
    @debug
    def list_all_stock(self) -> List[Dict[str, Any]]:
        """全部库存点。写入时按物料分命名空间，所以这里是跨命名空间的前缀查询。"""
        return self.list_namespace(("stock",))

    @debug
    def list_stock_by_material(self, material_code: str) -> List[Dict[str, Any]]:
        """某个物料在各仓库的库存分布。"""
        if not material_code:
            return []
        return self.list_namespace(("stock", material_code))

    @debug
    def list_stock_by_warehouse(self, warehouse_code: str) -> List[Dict[str, Any]]:
        """某个仓库里各物料（盘点视角）。"""
        if not warehouse_code:
            return []
        return self.list_namespace(("stock_wh", warehouse_code))

    # ==================== 报价 ====================
    @debug
    def list_quotes_by_material(self, material_code: str) -> List[Dict[str, Any]]:
        """某个物料的各家供应商报价（比价的输入）。"""
        if not material_code:
            return []
        return self.list_namespace(("quote", material_code))

    @debug
    def get_quote(self, material_code: str, supplier_code: str) -> Dict[str, Any]:
        if not material_code or not supplier_code:
            return {}
        return self.get_item(("quote", material_code), supplier_code)

    # ==================== 采购订单 ====================
    @debug
    def get_purchase_order(self, po_no: str) -> Dict[str, Any]:
        if not po_no:
            return {}
        return self.get_item(("purchase_order",), po_no)

    @debug
    def list_all_purchase_orders(self, limit: int = _SEARCH_ALL) -> List[Dict[str, Any]]:
        return self.list_namespace(("purchase_order",), limit=limit)

    # ==================== 收货单 ====================
    @debug
    def list_all_receipts(self, limit: int = _SEARCH_ALL) -> List[Dict[str, Any]]:
        return self.list_namespace(("receipt",), limit=limit)

    # ==================== 需求计划 ====================
    @debug
    def list_all_demands(self) -> List[Dict[str, Any]]:
        return self.list_namespace(("demand",))
