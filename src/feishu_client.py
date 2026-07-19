"""
飞书多维表 API 客户端
支持读写核心表 holdings/cash_flow/nav_history/holdings_snapshot，以及可选 transactions/repair/schema 表。
"""
import json
import math
import time
import requests
import requests.adapters
import threading
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

from src import config


class FeishuBatchWriteError(RuntimeError):
    """A batch chunk was not fully confirmed by Feishu."""

    def __init__(
        self,
        *,
        operation: str,
        table_name: str,
        chunk_offset: int,
        reason: str,
        confirmed_results: Optional[List[Any]] = None,
    ):
        self.operation = operation
        self.table_name = table_name
        self.chunk_offset = chunk_offset
        self.confirmed_results = list(confirmed_results or [])
        super().__init__(
            f"Feishu batch {operation} failed: table={table_name}, "
            f"chunk_offset={chunk_offset}, confirmed={len(self.confirmed_results)}: {reason}"
        )


class FeishuClient:
    """飞书多维表 API 客户端"""

    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str = None, app_secret: str = None, user_token: str = None):
        """
        初始化飞书客户端

        Args:
            app_id: 飞书自建应用 App ID
            app_secret: 飞书自建应用 App Secret
            user_token: 个人访问令牌（与 app_id/app_secret 二选一）
        """
        self.app_id = app_id or config.get("feishu.app_id")
        self.app_secret = app_secret or config.get("feishu.app_secret")
        self.user_token = user_token or config.get("feishu.user_token")
        self.timeout = (
            config.get_float("feishu.connect_timeout", 5.0) or 5.0,
            config.get_float("feishu.read_timeout", 30.0) or 30.0,
        )

        # 应用级 token 缓存（带线程安全锁）
        self._tenant_token = None
        self._token_expire_time = 0
        self._token_lock = threading.Lock()  # 用于双重检查锁

        # 限流保护：飞书 API 限制 20 QPS
        self._last_request_time = 0
        self._min_interval = 0.06  # 60ms = 约 16 QPS，留有余量
        self._rate_lock = threading.Lock()

        # 表配置映射（支持两种配置方式）
        # 方式1（统一base）：FEISHU_APP_TOKEN=bascnxxx + FEISHU_TABLE_HOLDINGS=tblxxx
        # 方式2（分表base）：FEISHU_TABLE_HOLDINGS=bascnxxx/tblxxx
        self.table_configs = {}
        for table_name in ['holdings', 'transactions', 'price_cache', 'nav_history', 'cash_flow', 'holdings_snapshot', 'compensation_tasks', 'schema_version']:
            value = config.get(f"feishu.tables.{table_name}")
            if value:
                if '/' in value:
                    # 分表base配置: bascnxxx/tblxxx
                    parts = value.split('/')
                    self.table_configs[table_name] = {
                        'app_token': parts[0],
                        'table_id': parts[1] if len(parts) > 1 else value
                    }
                else:
                    # 统一base配置，table_id单独存储
                    self.table_configs[table_name] = {
                        'app_token': None,  # 使用统一的 FEISHU_APP_TOKEN
                        'table_id': value
                    }

        # 统一 base token（方式1使用，方式2中各表有自己的）
        self.default_app_token = config.get("feishu.app_token")

        # 连接池配置（提升HTTP请求效率）
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=3,
            pool_block=False
        )
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        if self.user_token:
            # 使用个人访问令牌
            return {
                'Authorization': f'Bearer {self.user_token}',
                'Content-Type': 'application/json'
            }
        else:
            # 使用应用级 token
            return {
                'Authorization': f'Bearer {self._get_tenant_token()}',
                'Content-Type': 'application/json'
            }

    def _get_tenant_token(self) -> str:
        """获取应用级 tenant access token（带缓存，线程安全 DCL）"""
        now = time.time()

        # 第一重检查（无锁）
        if self._tenant_token and now < self._token_expire_time - 300:
            return self._tenant_token

        # 获取锁进行第二重检查
        with self._token_lock:
            # 第二重检查（有锁）- 防止多个线程同时通过第一重检查后重复请求
            if self._tenant_token and now < self._token_expire_time - 300:
                return self._tenant_token

            if not self.app_id or not self.app_secret:
                raise ValueError("需要提供 app_id 和 app_secret，请在 config.yaml 或环境变量中配置")

            url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
            response = requests.post(url, json={
                'app_id': self.app_id,
                'app_secret': self.app_secret
            }, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            if data.get('code') != 0:
                raise Exception(f"获取 token 失败: {data.get('msg')}")

            self._tenant_token = data['tenant_access_token']
            self._token_expire_time = now + data['expire']
            return self._tenant_token

    def _rate_limit(self):
        """限流控制（线程安全）"""
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.time()

    def _effective_timeout(self, requested: Any = None) -> tuple[float, float]:
        if requested is None:
            return self.timeout
        if isinstance(requested, (tuple, list)) and len(requested) == 2:
            connect, read = float(requested[0]), float(requested[1])
        else:
            connect = read = float(requested)
        if not math.isfinite(connect) or not math.isfinite(read) or connect <= 0 or read <= 0:
            raise ValueError("timeout values must be positive")
        return min(connect, self.timeout[0]), min(read, self.timeout[1])

    def _request(self, method: str, endpoint: str, _retry_count: int = 0, **kwargs) -> Dict:
        """发送请求（带限流和错误处理）"""
        self._rate_limit()

        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers()

        kwargs['timeout'] = self._effective_timeout(kwargs.get('timeout'))
        response = self.session.request(method, url, headers=headers, **kwargs)

        # 处理限流错误（最多重试3次）
        if response.status_code == 429:
            if _retry_count >= 3:
                response.raise_for_status()
            time.sleep(1 * (2 ** _retry_count))  # 指数退避
            return self._request(method, endpoint, _retry_count=_retry_count + 1, **kwargs)

        response.raise_for_status()
        data = response.json()

        if data.get('code') != 0:
            raise Exception(f"飞书 API 错误: {data.get('msg')} (code={data.get('code')})")

        return data.get('data', {})

    def _get_table_config(self, table_name: str) -> tuple:
        """获取表的配置 (app_token, table_id)"""
        config = self.table_configs.get(table_name)
        if not config:
            raise ValueError(f"未配置表 {table_name}，请设置 FEISHU_TABLE_{table_name.upper()}")

        app_token = config['app_token'] or self.default_app_token
        table_id = config['table_id']

        if not app_token:
            raise ValueError(f"未配置 FEISHU_APP_TOKEN 或表 {table_name} 的分表 token")
        if not table_id:
            raise ValueError(f"未配置表 {table_name} 的 table ID")

        return app_token, table_id

    def send_text_message(self, *, open_id: str, text: str) -> Dict[str, Any]:
        """Send a plain-text message from the configured Feishu app."""
        open_id_value = str(open_id or '').strip()
        text_value = str(text or '').strip()
        if not open_id_value:
            raise ValueError("open_id is required")
        if not text_value:
            raise ValueError("text is required")

        data = self._request(
            'POST',
            '/im/v1/messages',
            params={'receive_id_type': 'open_id'},
            json={
                'receive_id': open_id_value,
                'msg_type': 'text',
                'content': json.dumps({'text': text_value}, ensure_ascii=False),
            },
        )
        return {
            'success': True,
            'message_id': data.get('message_id'),
            'receive_id_type': 'open_id',
        }

    def list_records(self, table_name: str, filter_str: str = None,
                     field_names: List[str] = None, page_size: int = 500) -> List[Dict]:
        """
        查询记录列表

        Args:
            table_name: 表名（核心 holdings/cash_flow/nav_history/holdings_snapshot；可选 transactions 等）
            filter_str: 筛选条件（飞书 filter 语法）
            field_names: 指定返回的字段列表（减少数据传输）
            page_size: 每页数量
        """
        app_token, table_id = self._get_table_config(table_name)

        records = []
        page_token = None

        while True:
            endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            params = {'page_size': page_size}
            if page_token:
                params['page_token'] = page_token
            if filter_str:
                params['filter'] = filter_str
            if field_names:
                # 飞书API使用field_names参数指定返回字段
                params['field_names'] = json.dumps(field_names)

            data = self._request('GET', endpoint, params=params)
            items = data.get('items', [])

            for item in items:
                record = {
                    'record_id': item['record_id'],
                    'fields': item['fields']
                }
                records.append(record)

            page_token = data.get('page_token')
            if not page_token or not items:
                break

        return records

    def get_record_strict(self, table_name: str, record_id: str) -> Dict:
        """获取单条记录（严格模式）：任何错误直接抛出。"""
        app_token, table_id = self._get_table_config(table_name)
        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"

        data = self._request('GET', endpoint)
        # API returns {'record': {...}} for get_record
        rec = data.get('record') if isinstance(data, dict) else None
        if rec and isinstance(rec, dict):
            return {
                'record_id': rec.get('record_id'),
                'fields': rec.get('fields')
            }
        # fallback (defensive)
        if isinstance(data, dict) and ('record_id' in data or 'fields' in data):
            return {
                'record_id': data.get('record_id'),
                'fields': data.get('fields')
            }
        raise ValueError(f"Unexpected get_record response shape for table={table_name}: {data}")

    # 各表必填字段定义（用于验证）
    REQUIRED_FIELDS = {
        'holdings': ['asset_id', 'account', 'quantity'],
        'transactions': ['tx_date', 'tx_type', 'asset_id', 'account', 'quantity', 'price'],
        'cash_flow': ['flow_date', 'account', 'amount', 'currency'],
        'nav_history': ['date', 'account', 'total_value', 'shares', 'nav'],
        'price_cache': ['asset_id', 'price', 'currency', 'cny_price'],
        # Per-NAV-date holdings snapshot for audit/repro.
        'holdings_snapshot': ['as_of', 'account', 'asset_id', 'broker', 'quantity', 'currency', 'price', 'cny_price', 'market_value_cny', 'dedup_key'],
    }

    def create_record(self, table_name: str, fields: Dict[str, Any]) -> Dict:
        """
        创建记录

        Args:
            table_name: 表名
            fields: 字段值字典
        """
        app_token, table_id = self._get_table_config(table_name)

        # 验证必填字段
        required = self.REQUIRED_FIELDS.get(table_name, [])
        for field in required:
            if field not in fields or fields[field] is None or fields[field] == '':
                raise ValueError(f"表 {table_name} 缺少必填字段: {field}")

        # 过滤空值字段（避免创建空记录）
        filtered_fields = {k: v for k, v in fields.items() if v is not None and v != ''}

        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"

        data = self._request('POST', endpoint, json={'fields': filtered_fields})
        return {
            'record_id': data['record']['record_id'],
            'fields': data['record']['fields']
        }

    def update_record(self, table_name: str, record_id: str, fields: Dict[str, Any]) -> Dict:
        """更新记录"""
        app_token, table_id = self._get_table_config(table_name)

        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"

        data = self._request('PUT', endpoint, json={'fields': fields})
        return {
            'record_id': data['record']['record_id'],
            'fields': data['record']['fields']
        }

    def delete_record(self, table_name: str, record_id: str) -> bool:
        """删除记录"""
        app_token, table_id = self._get_table_config(table_name)

        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        self._request('DELETE', endpoint)
        return True

    @staticmethod
    def _normalize_batch_record(record: Any) -> Dict[str, Any]:
        if not isinstance(record, dict):
            raise ValueError(f"record is not an object: {record!r}")
        nested = record.get('record')
        if isinstance(nested, dict):
            record = nested
        record_id = str(record.get('record_id') or '').strip()
        if not record_id:
            raise ValueError(f"record_id missing: {record!r}")
        normalized = dict(record)
        normalized['record_id'] = record_id
        normalized['fields'] = dict(record.get('fields') or {})
        return normalized

    def _validate_batch_records(
        self,
        *,
        operation: str,
        table_name: str,
        requested: List[Dict[str, Any]],
        data: Dict[str, Any],
        chunk_offset: int,
        confirmed_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        raw_records = data.get('records') if isinstance(data, dict) else None
        if not isinstance(raw_records, list):
            raise FeishuBatchWriteError(
                operation=operation,
                table_name=table_name,
                chunk_offset=chunk_offset,
                reason="response records is missing or not a list",
                confirmed_results=confirmed_results,
            )
        if len(raw_records) != len(requested):
            raise FeishuBatchWriteError(
                operation=operation,
                table_name=table_name,
                chunk_offset=chunk_offset,
                reason=f"response cardinality {len(raw_records)} != request cardinality {len(requested)}",
                confirmed_results=confirmed_results,
            )
        try:
            normalized = [self._normalize_batch_record(record) for record in raw_records]
        except ValueError as exc:
            raise FeishuBatchWriteError(
                operation=operation,
                table_name=table_name,
                chunk_offset=chunk_offset,
                reason=str(exc),
                confirmed_results=confirmed_results,
            ) from exc

        if operation == 'update':
            expected_ids = [str(record.get('record_id') or '').strip() for record in requested]
            if any(not record_id for record_id in expected_ids) or len(set(expected_ids)) != len(expected_ids):
                raise ValueError("batch update requires unique non-empty record_id values")
            by_id = {record['record_id']: record for record in normalized}
            if len(by_id) != len(normalized) or set(by_id) != set(expected_ids):
                raise FeishuBatchWriteError(
                    operation=operation,
                    table_name=table_name,
                    chunk_offset=chunk_offset,
                    reason=f"response record IDs do not match request IDs: expected={expected_ids}, actual={list(by_id)}",
                    confirmed_results=confirmed_results,
                )
            normalized = [by_id[record_id] for record_id in expected_ids]
        return normalized

    def _request_batch_chunk(
        self,
        *,
        operation: str,
        table_name: str,
        endpoint: str,
        batch: List[Dict[str, Any]],
        chunk_offset: int,
        confirmed_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        try:
            data = self._request('POST', endpoint, json={'records': batch})
        except FeishuBatchWriteError:
            raise
        except Exception as exc:
            raise FeishuBatchWriteError(
                operation=operation,
                table_name=table_name,
                chunk_offset=chunk_offset,
                reason=str(exc),
                confirmed_results=confirmed_results,
            ) from exc
        return self._validate_batch_records(
            operation=operation,
            table_name=table_name,
            requested=batch,
            data=data,
            chunk_offset=chunk_offset,
            confirmed_results=confirmed_results,
        )

    def batch_create_records(self, table_name: str, records: List[Dict[str, Any]]) -> List[Dict]:
        """
        批量创建记录（减少 API 调用次数）

        Args:
            table_name: 表名
            records: 字段值字典列表
        """
        if not records:
            return []

        app_token, table_id = self._get_table_config(table_name)

        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"

        # 飞书限制单次最多 500 条
        batch_size = 500
        results = []

        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            results.extend(self._request_batch_chunk(
                operation='create',
                table_name=table_name,
                endpoint=endpoint,
                batch=batch,
                chunk_offset=i,
                confirmed_results=results,
            ))

        return results

    def batch_update_records(self, table_name: str, records: List[Dict]) -> List[Dict]:
        """
        批量更新记录

        Args:
            table_name: 表名
            records: [{'record_id': str, 'fields': dict}, ...]
        """
        if not records:
            return []
        record_ids = [str(record.get('record_id') or '').strip() for record in records]
        if any(not record_id for record_id in record_ids) or len(set(record_ids)) != len(record_ids):
            raise ValueError("batch update requires unique non-empty record_id values")

        app_token, table_id = self._get_table_config(table_name)

        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"

        batch_size = 500
        results = []

        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            results.extend(self._request_batch_chunk(
                operation='update',
                table_name=table_name,
                endpoint=endpoint,
                batch=batch,
                chunk_offset=i,
                confirmed_results=results,
            ))

        return results

    def batch_delete_records(self, table_name: str, record_ids: List[str]) -> int:
        """
        批量删除记录

        Args:
            table_name: 表名
            record_ids: 记录ID列表

        Returns:
            删除的记录数
        """
        if not record_ids:
            return 0

        app_token, table_id = self._get_table_config(table_name)

        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"

        batch_size = 500
        deleted_count = 0

        for i in range(0, len(record_ids), batch_size):
            batch = record_ids[i:i + batch_size]
            try:
                data = self._request('POST', endpoint, json={'records': batch})
            except Exception as exc:
                raise FeishuBatchWriteError(
                    operation='delete',
                    table_name=table_name,
                    chunk_offset=i,
                    reason=str(exc),
                    confirmed_results=record_ids[:deleted_count],
                ) from exc
            raw_records = data.get('records') if isinstance(data, dict) else None
            if not isinstance(raw_records, list) or len(raw_records) != len(batch):
                actual = len(raw_records) if isinstance(raw_records, list) else 'missing'
                raise FeishuBatchWriteError(
                    operation='delete',
                    table_name=table_name,
                    chunk_offset=i,
                    reason=f"response cardinality {actual} != request cardinality {len(batch)}",
                    confirmed_results=record_ids[:deleted_count],
                )
            actual_ids = [
                str(record.get('record_id') if isinstance(record, dict) else record).strip()
                for record in raw_records
            ]
            if set(actual_ids) != set(batch) or len(set(actual_ids)) != len(batch):
                raise FeishuBatchWriteError(
                    operation='delete',
                    table_name=table_name,
                    chunk_offset=i,
                    reason=f"response record IDs do not match request IDs: expected={batch}, actual={actual_ids}",
                    confirmed_results=record_ids[:deleted_count],
                )
            deleted_count += len(raw_records)

        return deleted_count
