"""存储后端工厂：仅保留 Feishu 多维表作为唯一存储后端。"""
from __future__ import annotations

from .feishu_storage import FeishuStorage


def _feishu_healthcheck(storage: FeishuStorage) -> None:
    """做一次最小化远程探活，确认资源权限可用。"""
    app_token, table_id = storage.client._get_table_config('holdings')
    storage.client._request(
        'GET',
        f'/bitable/v1/apps/{app_token}/tables/{table_id}/records',
        params={'page_size': 1},
    )


def create_storage(*, healthcheck: bool = True):
    """创建 Feishu 存储后端。"""
    storage = FeishuStorage()
    if healthcheck:
        _feishu_healthcheck(storage)
    return storage
