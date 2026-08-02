"""测试飞书存储层"""
import pytest
from dataclasses import replace
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
import json

from src.feishu_storage import FeishuStorage
from src.feishu.errors import FeishuRecordNotFoundError, LegacyReadOnlyError
from src.feishu.contracts import (
    TABLE_CONTRACTS,
    FieldEncoding,
    compare_live_schema,
)
from src.domain.compensation_contracts import MIRROR_COMPENSATION_STATUS_VALUES
from src.domain.cash_flow_contracts import (
    CashFlowContractError,
    CompletedCashFlowFacts,
)
from src.domain.holding_mutations import (
    HoldingMutationConflictError,
    HoldingMutationProofError,
    HoldingTarget,
)
from src.models import (
    ArchivedTransaction, Holding, Transaction, CashFlow, NAVHistory, PriceCache,
    AssetType, TransactionType, AssetClass, Industry, make_cf_dedup_key
)


def _assert_canonical_holding_date(value):
    assert isinstance(value, str)
    parsed = datetime.strptime(value, '%Y/%m/%d')
    assert parsed.strftime('%Y/%m/%d') == value


def _completed_cash_flow(
    *,
    amount=100,
    flow_date=date(2025, 3, 14),
    account='测试账户',
    broker='华泰',
    currency='CNY',
    exchange_rate=None,
    source='test',
    record_id='',
):
    rate = exchange_rate
    if rate is None:
        rate = 1 if currency == 'CNY' else 7.2
    return CompletedCashFlowFacts.build(
        flow_date=flow_date,
        account=account,
        broker=broker,
        amount=amount,
        currency=currency,
        exchange_rate=rate,
        cny_amount=float(amount) * float(rate),
        source=source,
        record_id=record_id,
    )


def _archived_transaction_fields(
    *,
    account='测试账户',
    request_id='req-123',
    dedup_key='dedup-123',
    tx_date='2025-03-14',
    tx_type='BUY',
    asset_id='000001',
    quantity=1000,
    price=10.5,
    currency='CNY',
    **optional,
):
    return {
        'request_id': request_id,
        'dedup_key': dedup_key,
        'tx_date': tx_date,
        'tx_type': tx_type,
        'asset_id': asset_id,
        'account': account,
        'quantity': quantity,
        'price': price,
        'currency': currency,
        **optional,
    }


def _compensation_task_fields(**changes):
    fields = {
        "task_id": "repair-1",
        "operation_type": "SELL_TARGETS_INCOMPLETE",
        "account": "a",
        "status": "PENDING",
        "payload": {"targets": [{"type": "HOLDING_TARGET_SET"}]},
        "error": "initial failure",
        "related_record_id": "nav-1",
        "retry_count": 0,
        "created_at": "2026-08-02T09:00:00",
        "updated_at": "2026-08-02T09:00:00",
    }
    fields.update(changes)
    return fields


class TestFeishuStorageInitialization:
    """测试飞书存储层初始化"""

    def test_init_with_client(self):
        """测试使用客户端初始化"""
        mock_client = Mock()
        storage = FeishuStorage(client=mock_client)
        assert storage.client == mock_client

    @patch('src.feishu_storage.FeishuClient')
    def test_init_auto_create_client(self, mock_client_class):
        """测试自动创建客户端"""
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        storage = FeishuStorage()
        assert storage.client == mock_client


class TestFeishuStorageFieldConversion:
    """测试飞书存储层字段转换"""

    def setup_method(self):
        """每个测试方法前执行"""
        self.storage = FeishuStorage(client=Mock())

    def test_to_feishu_fields_holdings(self):
        """测试持仓表字段转换"""
        data = {
            'asset_id': '000001',
            'asset_name': '平安银行',
            'quantity': 1000.5,
            'avg_cost': 10.5,
            'tag': ['银行', '金融']
        }
        result = self.storage._to_feishu_fields(data, 'holdings')

        assert result['asset_id'] == '000001'
        assert result['quantity'] == 1000.5  # 数字类型
        assert result['avg_cost'] == 10.5    # 数字类型
        assert result['tag'] == json.dumps(['银行', '金融'], ensure_ascii=False)

    def test_to_feishu_fields_transactions(self):
        """测试交易表字段转换（数字类型）"""
        data = {
            'asset_id': '000001',
            'quantity': 100,
            'price': 10.5,
            'amount': 1050.0,
            'fee': 5.0
        }
        result = self.storage._to_feishu_fields(data, 'transactions')

        assert result['quantity'] == 100
        assert result['price'] == 10.5
        assert result['amount'] == 1050.0
        assert result['fee'] == 5.0

    def test_to_feishu_fields_dates(self):
        """测试日期字段转换为 Unix 时间戳（毫秒）"""
        data = {
            'tx_date': date(2025, 3, 14),
            'created_at': datetime(2025, 3, 14, 10, 30, 0)
        }
        result = self.storage._to_feishu_fields(data, 'transactions')

        # 日期字段应转换为 Unix 时间戳（毫秒）
        assert isinstance(result['tx_date'], int)
        # 验证时间戳对应正确日期
        from datetime import timezone
        restored_date = datetime.fromtimestamp(result['tx_date'] / 1000).date()
        assert restored_date == date(2025, 3, 14)
        # datetime 也应转换为时间戳
        assert isinstance(result['created_at'], int)
        restored_dt = datetime.fromtimestamp(result['created_at'] / 1000)
        assert restored_dt.date() == date(2025, 3, 14)

    def test_to_feishu_fields_enums(self):
        """测试枚举字段转换"""
        data = {
            'asset_type': AssetType.A_STOCK,
            'tx_type': TransactionType.BUY,
            'asset_class': AssetClass.CN_ASSET,
            'industry': Industry.FINANCE
        }
        result = self.storage._to_feishu_fields(data, 'transactions')

        assert result['asset_type'] == 'a_stock'
        assert result['tx_type'] == 'BUY'
        assert result['asset_class'] == '中国资产'
        assert result['industry'] == '金融'

    def test_to_feishu_fields_asset_id(self):
        """测试asset_id特殊处理"""
        data = {'asset_id': 123456}  # 数字类型
        result = self.storage._to_feishu_fields(data, 'holdings')

        assert result['asset_id'] == '123456'  # 转为字符串

    def test_from_feishu_fields_holdings(self):
        """测试持仓表字段反向转换"""
        fields = {
            'asset_id': '000001',
            'asset_name': '平安银行',
            'quantity': '1000.5',
            'avg_cost': '10.5',
            'tag': '["银行", "金融"]'
        }
        result = self.storage._from_feishu_fields(fields, 'holdings')

        assert result['asset_id'] == '000001'
        assert result['quantity'] == 1000.5
        assert result['avg_cost'] == 10.5
        assert result['tag'] == ['银行', '金融']

    def test_from_feishu_fields_transactions(self):
        """测试交易表字段反向转换"""
        fields = {
            'asset_id': '000001',
            'quantity': '100',
            'price': '10.5',
            'amount': '1050.0'
        }
        result = self.storage._from_feishu_fields(fields, 'transactions')

        assert result['quantity'] == 100.0
        assert result['price'] == 10.5
        assert result['amount'] == 1050.0

    def test_from_feishu_fields_nav_history_details(self):
        """测试净值表details字段"""
        details = {'daily_pnl': 1000.0, 'nav_change': 0.05}
        fields = {
            'total_value': '1000000',
            'details': json.dumps(details, ensure_ascii=False)
        }
        result = self.storage._from_feishu_fields(fields, 'nav_history')

        assert result['total_value'] == 1000000.0
        assert result['details'] == details

    def test_from_feishu_fields_none_values(self):
        """测试空值处理"""
        fields = {
            'asset_id': '000001',
            'avg_cost': None,
            'quantity': ''
        }
        result = self.storage._from_feishu_fields(fields, 'holdings')

        assert result['asset_id'] == '000001'
        assert result['avg_cost'] is None
        assert result['quantity'] == ''

    def test_zero_values_are_not_dropped_in_conversion(self):
        tx_fields = self.storage._from_feishu_fields({'amount': '0', 'fee': '0', 'tax': '0'}, 'transactions')
        cf_fields = self.storage._from_feishu_fields({'amount': '0', 'cny_amount': '0', 'exchange_rate': '0'}, 'cash_flow')
        price = self.storage._dict_to_price_cache({'asset_id': 'AAPL', 'price': 10.0, 'currency': 'USD', 'cny_price': 0.0, 'change': 0.0, 'change_pct': 0.0, 'exchange_rate': 0.0})

        assert tx_fields['amount'] == 0.0
        assert tx_fields['fee'] == 0.0
        assert tx_fields['tax'] == '0'
        assert cf_fields['amount'] == 0.0
        assert cf_fields['cny_amount'] == 0.0
        assert cf_fields['exchange_rate'] == 0.0
        assert price.cny_price == 0.0
        assert price.change == 0.0
        assert price.change_pct == 0.0
        assert price.exchange_rate == 0.0

    def test_registered_number_fields_share_registry_owned_read_conversion(self):
        for table_name, contract in TABLE_CONTRACTS.items():
            fields = {
                field.name: '1.25'
                for field in contract.fields
                if field.encoding is FieldEncoding.NUMBER
            }
            if not fields:
                continue
            converted = self.storage._from_feishu_fields(fields, table_name)

            assert converted
            assert all(isinstance(value, float) for value in converted.values())

    def test_registered_json_text_fields_share_registry_owned_conversion(self):
        encoded = self.storage._to_feishu_fields(
            {'payload': {'target': 1}},
            'compensation_tasks',
        )
        decoded = self.storage._from_feishu_fields(encoded, 'compensation_tasks')

        assert encoded['payload'] == '{"target": 1}'
        assert decoded['payload'] == {'target': 1}


class TestFeishuStorageEscapeFilter:
    """测试飞书存储层filter转义"""

    def setup_method(self):
        self.storage = FeishuStorage(client=Mock())

    def test_escape_filter_simple(self):
        """测试简单字符串转义"""
        result = self.storage._escape_filter_value('test_value')
        assert result == 'test_value'

    def test_escape_filter_with_quotes(self):
        """测试带引号的转义"""
        result = self.storage._escape_filter_value('value with "quotes"')
        assert result == 'value with \\"quotes\\"'

    def test_escape_filter_with_backslash(self):
        """测试带反斜杠的转义"""
        result = self.storage._escape_filter_value('value with \\ backslash')
        # 反斜杠转义为 \\
        assert '\\\\' in result

    def test_escape_filter_non_string(self):
        """测试非字符串转义"""
        result = self.storage._escape_filter_value(123)
        assert result == '123'


class TestFeishuStorageCompensationMirror:
    def setup_method(self):
        self.client = Mock()
        self.client._get_table_config.return_value = ("app", "table")
        self.storage = FeishuStorage(client=self.client)

    def test_unconfigured_optional_table_is_an_explicit_zero_request_skip(self):
        self.client._get_table_config.side_effect = ValueError(
            "未配置表 compensation_tasks"
        )

        result = self.storage.mirror_compensation_task(
            _compensation_task_fields()
        )

        assert result["status"] == "skipped_unconfigured"
        assert "未配置表" in result["error"]
        self.client.list_records.assert_not_called()
        self.client.create_record.assert_not_called()
        self.client.update_record.assert_not_called()

    def test_zero_fresh_matches_creates_registry_encoded_projection(self):
        self.client.list_records.return_value = []

        def create(_table, fields):
            return {
                "record_id": "mirror-1",
                "fields": dict(fields),
            }

        self.client.create_record.side_effect = create

        result = self.storage.mirror_compensation_task(
            _compensation_task_fields(task_id='repair-"quoted"')
        )

        assert result == {
            "status": "created",
            "task_id": 'repair-"quoted"',
            "record_id": "mirror-1",
        }
        _, list_kwargs = self.client.list_records.call_args
        assert '\\"quoted\\"' in list_kwargs["filter_str"]
        _, create_fields = self.client.create_record.call_args.args
        assert create_fields["task_id"] == 'repair-"quoted"'
        assert create_fields["status"] == "PENDING"
        assert json.loads(create_fields["payload"])["targets"]
        assert create_fields["retry_count"] == 0

    def test_one_fresh_match_updates_and_resolved_clears_stale_error(self):
        self.client.list_records.return_value = [{
            "record_id": "mirror-1",
            "fields": {"task_id": "repair-1"},
        }]

        def update(_table, record_id, fields):
            return {"record_id": record_id, "fields": dict(fields)}

        self.client.update_record.side_effect = update
        task = _compensation_task_fields(
            status="RESOLVED",
            error="",
            retry_count=2,
            updated_at="2026-08-02T09:03:00",
            resolved_at="2026-08-02T09:03:00",
            resolution="targets_applied_and_read_back",
        )

        result = self.storage.mirror_compensation_task(task)

        assert result["status"] == "updated"
        assert result["record_id"] == "mirror-1"
        _, record_id, update_fields = self.client.update_record.call_args.args
        assert record_id == "mirror-1"
        assert update_fields["status"] == "RESOLVED"
        assert update_fields["retry_count"] == 2
        assert update_fields["updated_at"] == "2026-08-02T09:03:00"
        assert update_fields["resolved_at"] == "2026-08-02T09:03:00"
        assert update_fields["resolution"] == "targets_applied_and_read_back"
        assert update_fields["error"] is None
        contract = TABLE_CONTRACTS["compensation_tasks"]
        assert (
            contract.fields_by_name["status"].select_options
            == MIRROR_COMPENSATION_STATUS_VALUES
        )
        assert contract.fields_by_name["error"].clearable is True
        assert contract.fields_by_name["error"].schema_required is True
        assert "error" not in contract.write_contract("create").required_fields
        self.client.create_record.assert_not_called()

    def test_error_is_schema_required_even_though_create_row_may_omit_it(self):
        contract = TABLE_CONTRACTS["compensation_tasks"]
        live_without_error = [
            {
                "field_name": field.name,
                "type": field.type_id,
                "ui_type": field.ui_type,
                "property": {
                    "options": [
                        {"name": option}
                        for option in field.select_options
                    ],
                },
            }
            for field in contract.fields
            if field.name != "error"
        ]

        result = compare_live_schema(contract, live_without_error)

        assert result["ok"] is False
        assert result["missing_required"] == ["error"]
        assert result["missing_optional"] == []

    def test_duplicate_fresh_task_ids_fail_mirror_without_mutation(self):
        self.client.list_records.return_value = [
            {"record_id": "mirror-1", "fields": {"task_id": "repair-1"}},
            {"record_id": "mirror-2", "fields": {"task_id": "repair-1"}},
        ]

        result = self.storage.mirror_compensation_task(
            _compensation_task_fields()
        )

        assert result["status"] == "duplicate"
        assert result["matched_count"] == 2
        assert result["record_ids"] == ["mirror-1", "mirror-2"]
        self.client.create_record.assert_not_called()
        self.client.update_record.assert_not_called()

    def test_stale_local_record_id_recovers_through_fresh_lookup(self):
        self.client.update_record.side_effect = [
            FeishuRecordNotFoundError(code=1254043, message="missing"),
            {
                "record_id": "mirror-new",
                "fields": {"task_id": "repair-1"},
            },
        ]
        self.client.list_records.return_value = [{
            "record_id": "mirror-new",
            "fields": {"task_id": "repair-1"},
        }]

        result = self.storage.mirror_compensation_task(
            _compensation_task_fields(status="RUNNING"),
            mirror_record_id="mirror-stale",
        )

        assert result == {
            "status": "updated",
            "task_id": "repair-1",
            "record_id": "mirror-new",
        }
        assert self.client.update_record.call_count == 2
        self.client.list_records.assert_called_once()
        self.client.create_record.assert_not_called()


class TestFeishuStorageHoldingOperations:
    """测试飞书存储层持仓操作"""

    def setup_method(self):
        self.mock_client = Mock()
        self.storage = FeishuStorage(client=self.mock_client)

    def _use_remote_records(self, records, *, create_record_id='new_rec_123'):
        remote = [
            {'record_id': row['record_id'], 'fields': dict(row.get('fields') or {})}
            for row in records
        ]

        def list_records(_table_name, filter_str=None, **_kwargs):
            account = None
            if filter_str and 'CurrentValue.[account] = "' in filter_str:
                account = filter_str.split('CurrentValue.[account] = "', 1)[1].split('"', 1)[0]
            return [
                {'record_id': row['record_id'], 'fields': dict(row['fields'])}
                for row in remote
                if account is None or row['fields'].get('account') == account
            ]

        def get_record_strict(_table_name, record_id):
            row = next(item for item in remote if item['record_id'] == record_id)
            return {'record_id': row['record_id'], 'fields': dict(row['fields'])}

        def create_record(_table_name, fields):
            remote.append({'record_id': create_record_id, 'fields': dict(fields)})
            return {'record_id': create_record_id, 'fields': dict(fields)}

        def update_record(_table_name, record_id, fields):
            row = next(item for item in remote if item['record_id'] == record_id)
            row['fields'].update(fields)
            return {'record_id': record_id, 'fields': dict(row['fields'])}

        def delete_record(_table_name, record_id):
            remote[:] = [item for item in remote if item['record_id'] != record_id]
            return True

        self.mock_client.list_records.side_effect = list_records
        self.mock_client.get_record_strict.side_effect = get_record_strict
        self.mock_client.create_record.side_effect = create_record
        self.mock_client.update_record.side_effect = update_record
        self.mock_client.delete_record.side_effect = delete_record
        return remote

    def test_get_holding_with_market(self):
        """测试获取指定市场的持仓"""
        self.mock_client.list_records.return_value = [{
            'record_id': 'rec_123',
            'fields': {
                'asset_id': '00700',
                'asset_name': '腾讯控股',
                'asset_type': 'hk_stock',
                'account': '港股账户',
                'broker': '富途',
                'quantity': '100',
                'currency': 'HKD'
            }
        }]

        result = self.storage.get_holding('00700', '港股账户', broker='富途')

        assert result is not None
        assert result.asset_id == '00700'
        assert result.broker == '富途'
        assert result.quantity == 100.0

    def test_get_holding_without_market(self):
        """测试获取持仓（不指定市场）"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'rec_1',
                'fields': {
                    'asset_id': '000001',
                    'asset_name': '平安银行',
                    'asset_type': 'a_stock',
                    'account': '测试账户',
                    'broker': '华泰',
                    'quantity': '100',
                    'currency': 'CNY'
                }
            },
            {
                'record_id': 'rec_2',
                'fields': {
                    'asset_id': '000001',
                    'asset_name': '平安银行',
                    'asset_type': 'a_stock',
                    'account': '测试账户',
                    'broker': '手工',
                    'quantity': '200',
                    'currency': 'CNY'
                }
            }
        ]

        with pytest.raises(ValueError, match="requires broker"):
            self.storage.get_holding('000001', '测试账户')

        self.mock_client.create_record.assert_not_called()
        self.mock_client.update_record.assert_not_called()
        self.mock_client.delete_record.assert_not_called()

    def test_get_holding_not_found(self):
        """测试持仓不存在"""
        self.mock_client.list_records.return_value = []

        result = self.storage.get_holding('999999', '测试账户')

        assert result is None

    def test_get_holdings(self):
        """测试获取持仓列表"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'rec_1',
                'fields': {
                    'asset_id': '000001',
                    'asset_name': '平安银行',
                    'asset_type': 'a_stock',
                    'account': '测试账户',
                    'broker': '手工',
                    'quantity': '1000',
                    'currency': 'CNY'
                }
            },
            {
                'record_id': 'rec_2',
                'fields': {
                    'asset_id': '00700',
                    'asset_name': '腾讯控股',
                    'asset_type': 'hk_stock',
                    'account': '测试账户',
                    'broker': '手工',
                    'quantity': '0',  # 应该被过滤掉
                    'currency': 'HKD'
                }
            }
        ]

        holdings = self.storage.get_holdings(account='测试账户')

        assert len(holdings) == 1  # 数量为0的被过滤
        assert holdings[0].asset_id == '000001'

    def test_get_holdings_include_empty(self):
        """测试获取持仓列表包含空仓"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'rec_1',
                'fields': {
                    'asset_id': '000001',
                    'asset_name': '平安银行',
                    'asset_type': 'a_stock',
                    'account': '测试账户',
                    'broker': '手工',
                    'quantity': '100',
                    'currency': 'CNY'
                }
            },
            {
                'record_id': 'rec_2',
                'fields': {
                    'asset_id': '000002',
                    'asset_name': '万科',
                    'asset_type': 'a_stock',
                    'account': '测试账户',
                    'broker': '手工',
                    'quantity': '0',
                    'currency': 'CNY'
                }
            }
        ]

        holdings = self.storage.get_holdings(include_empty=True)

        assert len(holdings) == 2  # 包含数量为0的

    def test_upsert_holding_create(self):
        """测试创建新持仓"""
        self._use_remote_records([])

        holding = Holding(
            asset_id='000001',
            asset_name='平安银行',
            asset_type=AssetType.A_STOCK,
            account='测试账户',
            broker='手工',
            quantity=1000,
            currency='CNY'
        )

        result = self.storage.upsert_holding(holding)

        assert result.record_id == 'new_rec_123'
        self.mock_client.create_record.assert_called_once()

    def test_upsert_holding_canonicalizes_identity_payload_result_and_cache(self):
        self._use_remote_records([])

        result = self.storage.upsert_holding(Holding(
            asset_id=' AAPL ',
            asset_name=' Apple ',
            asset_type=AssetType.US_STOCK,
            account=' lx ',
            broker=' IBKR ',
            quantity=1,
            currency='usd',
        ))

        fields = self.mock_client.create_record.call_args.args[1]
        assert fields['asset_id'] == 'AAPL'
        assert fields['asset_name'] == 'Apple'
        assert fields['account'] == 'lx'
        assert fields['broker'] == 'IBKR'
        assert fields['currency'] == 'USD'
        assert result.asset_id == 'AAPL'
        assert result.account == 'lx'
        assert result.broker == 'IBKR'
        assert result.currency == 'USD'
        key = self.storage._get_holding_cache_key('AAPL', 'lx', 'IBKR')
        assert set(self.storage._holding_fields_cache) == {key}
        assert self.storage._holding_fields_cache[key]['currency'] == 'USD'

    def test_upsert_holding_update(self):
        """测试更新现有持仓"""
        self._use_remote_records([{
            'record_id': 'existing_rec',
            'fields': {
                'asset_id': '000001',
                'asset_name': '平安',
                'asset_type': 'a_stock',
                'account': '测试账户',
                'broker': '手工',
                'quantity': '500',
                'currency': 'CNY'
            }
        }])

        holding = Holding(
            asset_id='000001',
            asset_name='平安银行股份有限公司',
            asset_type=AssetType.A_STOCK,
            account='测试账户',
            broker='手工',
            quantity=1000,
            currency='CNY'
        )

        result = self.storage.upsert_holding(holding)

        assert result.record_id == 'existing_rec'
        self.mock_client.update_record.assert_called_once()
        update_fields = self.mock_client.update_record.call_args.args[2]
        assert update_fields['asset_name'] == '平安银行股份有限公司'
        _assert_canonical_holding_date(update_fields['updated_at'])

    def test_replace_holding_rejects_stale_readback_and_invalidates_cache(self):
        self._use_remote_records([{
            'record_id': 'existing_rec',
            'fields': {
                'asset_id': 'AAPL',
                'asset_name': 'Apple',
                'asset_type': 'us_stock',
                'account': 'lx',
                'broker': 'IBKR',
                'quantity': 1,
                'currency': 'USD',
            },
        }])
        self.mock_client.update_record.side_effect = lambda *_args, **_kwargs: {
            'record_id': 'existing_rec'
        }

        with pytest.raises(HoldingMutationProofError, match='readback disagrees'):
            self.storage.replace_holding(Holding(
                asset_id='AAPL',
                asset_name='Apple',
                asset_type=AssetType.US_STOCK,
                account='lx',
                broker='IBKR',
                quantity=2,
                currency='USD',
            ))

        key = self.storage._get_holding_cache_key('AAPL', 'lx', 'IBKR')
        assert key not in self.storage._holding_fields_cache

    def test_replace_holding_preserves_explicit_clear_intent(self):
        self._use_remote_records([{
            'record_id': 'existing_rec',
            'fields': {
                'asset_id': 'AAPL',
                'asset_name': 'Apple',
                'asset_type': 'us_stock',
                'account': 'lx',
                'broker': 'IBKR',
                'quantity': 1,
                'avg_cost': 200,
                'currency': 'USD',
                'asset_class': '美国资产',
                'industry': '科技',
                'tag': json.dumps(['manual'], ensure_ascii=False),
            },
        }])

        replaced = self.storage.replace_holding(Holding(
            asset_id='AAPL',
            asset_name='Apple',
            asset_type=AssetType.US_STOCK,
            account='lx',
            broker='IBKR',
            quantity=1,
            avg_cost=None,
            currency='USD',
            asset_class=None,
            industry=None,
            tag=[],
        ))

        fields = self.mock_client.update_record.call_args.args[2]
        assert fields['avg_cost'] is None
        assert fields['asset_class'] is None
        assert fields['industry'] is None
        assert fields['tag'] == '[]'
        assert replaced.avg_cost is None
        assert replaced.asset_class is None
        assert replaced.industry is None
        assert replaced.tag == []

    def test_update_holding_quantity(self):
        """测试更新持仓数量"""
        self._use_remote_records([{
            'record_id': 'rec_123',
            'fields': {
                'asset_id': '000001', 'asset_name': '平安银行',
                'asset_type': 'a_stock', 'account': '测试账户',
                'broker': '手工', 'quantity': '1000', 'currency': 'CNY',
            }
        }])

        self.storage.update_holding_quantity('000001', '测试账户', 500, '手工')

        self.mock_client.update_record.assert_called_once()
        call_args = self.mock_client.update_record.call_args
        assert call_args[0][2]['quantity'] == 1500  # 1000 + 500
        _assert_canonical_holding_date(call_args[0][2]['updated_at'])

    def test_delete_holding_if_zero(self):
        """测试持仓为0时删除"""
        self._use_remote_records([{
            'record_id': 'rec_123',
            'fields': {
                'asset_id': '000001', 'asset_name': '平安银行',
                'asset_type': 'a_stock', 'account': '测试账户',
                'broker': '手工', 'quantity': '0', 'currency': 'CNY',
            }
        }])

        self.storage.delete_holding_if_zero('000001', '测试账户', '手工')

        self.mock_client.delete_record.assert_called_once_with('holdings', 'rec_123')

    def test_delete_holding_if_not_zero(self):
        """测试持仓不为0时不删除"""
        self._use_remote_records([{
            'record_id': 'rec_123',
            'fields': {
                'asset_id': '000001', 'asset_name': '平安银行',
                'asset_type': 'a_stock', 'account': '测试账户',
                'broker': '手工', 'quantity': '100', 'currency': 'CNY',
            }
        }])

        self.storage.delete_holding_if_zero('000001', '测试账户', '手工')

        self.mock_client.delete_record.assert_not_called()

    def test_delete_holding_if_tiny_residual(self):
        """测试极小残值持仓会被视为零并删除"""
        self._use_remote_records([{
            'record_id': 'rec_123',
            'fields': {
                'asset_id': '000001', 'asset_name': '平安银行',
                'asset_type': 'a_stock', 'account': '测试账户',
                'broker': '手工', 'quantity': '0.0000000001', 'currency': 'CNY',
            }
        }])

        self.storage.delete_holding_if_zero('000001', '测试账户', '手工')

        self.mock_client.delete_record.assert_called_once_with('holdings', 'rec_123')

    def test_target_bound_zero_delete_refuses_reused_business_key(self):
        remote = self._use_remote_records([{
            'record_id': 'rec_old',
            'fields': {
                'asset_id': '000001', 'asset_name': '平安银行',
                'asset_type': 'a_stock', 'account': '测试账户',
                'broker': '手工', 'quantity': '0', 'currency': 'CNY',
            },
        }])
        base = self.storage.get_holding_fresh('000001', '测试账户', '手工')
        target = HoldingTarget.from_holdings(
            base=base,
            target=base,
            owned_fields={'quantity'},
        )
        remote[0]['record_id'] = 'rec_new'

        with pytest.raises(HoldingMutationConflictError, match='record changed'):
            self.storage.delete_holding_target_if_zero(target)

        self.mock_client.delete_record.assert_not_called()

    def test_delete_holding_failure_does_not_publish_unproved_cache(self):
        self._use_remote_records([{
            'record_id': 'rec_123',
            'fields': {
                'asset_id': '000001', 'asset_name': '平安银行',
                'asset_type': 'a_stock', 'account': '测试账户',
                'broker': '手工', 'quantity': '0', 'currency': 'CNY',
            }
        }])
        self.mock_client.delete_record.side_effect = RuntimeError('delete timeout')

        with pytest.raises(RuntimeError, match='delete timeout'):
            self.storage.delete_holding_if_zero('000001', '测试账户', '手工')

        cache_key = self.storage._get_holding_cache_key('000001', '测试账户', '手工')
        assert cache_key not in self.storage._holding_fields_cache

    def test_delete_holding_by_record_id(self):
        """测试通过记录ID删除持仓"""
        self._use_remote_records([{
            'record_id': 'rec_123',
            'fields': {
                'asset_id': '000001', 'asset_name': '平安银行',
                'asset_type': 'a_stock', 'account': '测试账户',
                'broker': '手工', 'quantity': '0', 'currency': 'CNY',
            },
        }])

        result = self.storage.delete_holding_by_record_id('rec_123')

        assert result == True
        self.mock_client.delete_record.assert_called_once_with('holdings', 'rec_123')


class TestFeishuStorageTransactionOperations:
    """测试飞书存储层交易操作"""

    def setup_method(self):
        self.mock_client = Mock()
        self.storage = FeishuStorage(client=self.mock_client)

    def _candidate_transaction(self):
        tx = Transaction(
            tx_date=date(2025, 3, 14),
            tx_type=TransactionType.BUY,
            asset_id='000001',
            account='测试账户',
            quantity=1000,
            price=10.5,
            currency='CNY',
            request_id='req_123'
        )
        return tx

    @pytest.mark.parametrize('boundary', ['facade', 'repository'])
    def test_add_transaction_is_a_read_only_tombstone_before_transport(self, boundary):
        target = self.storage if boundary == 'facade' else self.storage.transactions

        with pytest.raises(LegacyReadOnlyError, match='legacy read-only archive'):
            target.add_transaction(self._candidate_transaction())

        assert self.mock_client.mock_calls == []

    @pytest.mark.parametrize('boundary', ['facade', 'repository'])
    def test_delete_transaction_is_a_read_only_tombstone_before_transport(self, boundary):
        target = self.storage if boundary == 'facade' else self.storage.transactions

        with pytest.raises(LegacyReadOnlyError, match='legacy read-only archive'):
            target.delete_transaction_by_record_id('tx_rec')

        assert self.mock_client.mock_calls == []

    def test_get_transaction(self):
        """测试获取单条交易记录"""
        self.mock_client.get_record_strict.return_value = {
            'record_id': 'tx_rec',
            'fields': _archived_transaction_fields(),
        }

        result = self.storage.get_transaction('tx_rec')

        assert isinstance(result, ArchivedTransaction)
        assert result.asset_id == '000001'
        assert result.tx_type == TransactionType.BUY
        assert result.amount is None
        assert result.fee is None
        assert not hasattr(result, 'source')

    @pytest.mark.parametrize(
        ('field', 'bad_value'),
        [
            ('tx_type', None),
            ('currency', None),
            ('quantity', None),
            ('price', float('nan')),
            ('tx_date', 1741881600000),
            ('source', 'manual'),
        ],
    )
    def test_get_transaction_rejects_missing_or_invalid_archive_facts(
        self,
        field,
        bad_value,
    ):
        fields = _archived_transaction_fields()
        fields[field] = bad_value
        self.mock_client.get_record_strict.return_value = {
            'record_id': 'tx_bad',
            'fields': fields,
        }

        with pytest.raises(ValueError):
            self.storage.get_transaction('tx_bad')

    def test_get_transaction_not_found(self):
        """测试交易记录不存在"""
        self.mock_client.get_record_strict.side_effect = FeishuRecordNotFoundError(
            code=1254043,
            message='RecordIdNotFound',
        )

        result = self.storage.get_transaction('non_existent')

        assert result is None

    @pytest.mark.parametrize(
        "error",
        [
            PermissionError('forbidden'),
            TimeoutError('timeout'),
            ValueError('malformed response'),
        ],
    )
    def test_get_transaction_propagates_non_not_found_errors(self, error):
        self.mock_client.get_record_strict.side_effect = error

        with pytest.raises(type(error), match=str(error)):
            self.storage.get_transaction('rec_forbidden')

    def test_get_transactions(self):
        """测试获取交易记录列表"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'tx_1',
                'fields': _archived_transaction_fields(
                    request_id='req-1',
                    dedup_key='dedup-1',
                    tx_date='2025-03-14',
                    asset_id='000001',
                    tx_type='BUY',
                    quantity=1000,
                    price=10.5,
                )
            },
            {
                'record_id': 'tx_2',
                'fields': _archived_transaction_fields(
                    request_id='req-2',
                    dedup_key='dedup-2',
                    tx_date='2025-03-13',
                    asset_id='000002',
                    tx_type='SELL',
                    quantity=-500,
                    price=11,
                )
            }
        ]

        transactions = self.storage.get_transactions(account='测试账户')

        assert len(transactions) == 2
        # 按日期倒序排列
        assert transactions[0].tx_date == date(2025, 3, 14)
        assert transactions[1].tx_date == date(2025, 3, 13)

    def test_get_transactions_with_filter(self):
        """测试带筛选条件的交易查询"""
        self.mock_client.list_records.return_value = []

        self.storage.get_transactions(
            account='测试账户',
            start_date=date(2025, 3, 1),
            end_date=date(2025, 3, 14),
            tx_type='BUY'
        )

        call_args = self.mock_client.list_records.call_args
        assert 'filter_str' in call_args.kwargs
        filter_str = call_args.kwargs['filter_str']
        assert '测试账户' in filter_str
        assert 'BUY' in filter_str

    def test_archive_request_lookup_is_scoped_by_account_and_request_id(self):
        rows = {
            'account-a': {
                'record_id': 'tx-a',
                'fields': _archived_transaction_fields(
                    account='account-a',
                    request_id='shared',
                    dedup_key='dedup-a',
                ),
            },
            'account-b': {
                'record_id': 'tx-b',
                'fields': _archived_transaction_fields(
                    account='account-b',
                    request_id='shared',
                    dedup_key='dedup-b',
                ),
            },
        }

        def list_records(_table, *, filter_str):
            account = 'account-a' if 'account-a' in filter_str else 'account-b'
            return [rows[account]]

        self.mock_client.list_records.side_effect = list_records

        a = self.storage.find_archived_transaction_by_request_id(
            account='account-a',
            request_id='shared',
        )
        b = self.storage.find_archived_transaction_by_request_id(
            account='account-b',
            request_id='shared',
        )

        assert (a.record_id, a.account) == ('tx-a', 'account-a')
        assert (b.record_id, b.account) == ('tx-b', 'account-b')
        filters = [call.kwargs['filter_str'] for call in self.mock_client.list_records.call_args_list]
        assert all('CurrentValue.[account]' in value for value in filters)
        assert all('CurrentValue.[request_id]' in value for value in filters)

    def test_archive_request_lookup_rejects_cross_account_response(self):
        self.mock_client.list_records.return_value = [{
            'record_id': 'tx-a',
            'fields': _archived_transaction_fields(
                account='account-a',
                request_id='shared',
                dedup_key='dedup-a',
            ),
        }]

        with pytest.raises(ValueError, match='out-of-scope'):
            self.storage.find_archived_transaction_by_request_id(
                account='account-b',
                request_id='shared',
            )

    def test_archive_request_lookup_rejects_duplicate_business_key(self):
        fields = _archived_transaction_fields(
            account='account-a',
            request_id='shared',
            dedup_key='dedup-a',
        )
        self.mock_client.list_records.return_value = [
            {'record_id': 'tx-a-1', 'fields': fields},
            {'record_id': 'tx-a-2', 'fields': fields},
        ]

        with pytest.raises(ValueError, match='business key is ambiguous'):
            self.storage.find_archived_transaction_by_request_id(
                account='account-a',
                request_id='shared',
            )


class TestFeishuStorageCashFlowOperations:
    """测试飞书存储层出入金操作"""

    def setup_method(self):
        self.mock_client = Mock()
        self.storage = FeishuStorage(client=self.mock_client)

    def test_add_cash_flow(self):
        """测试添加出入金记录"""
        self.mock_client.list_records.return_value = []
        self.mock_client.create_record.return_value = {
            'record_id': 'cf_rec_123',
            'fields': {}
        }
        self.storage._cash_flow_agg_loaded_accounts.add('测试账户')
        self.storage._local_cash_flow_agg_cache.set_account(
            '测试账户',
            {'cumulative': 0.0, 'flow_count': 0},
        )

        cf = _completed_cash_flow(amount=100000)

        result = self.storage.add_cash_flow(cf)

        assert result.record_id == 'cf_rec_123'
        assert self.storage.get_cash_flow_aggs('测试账户')['cumulative'] == 100000.0

    def test_add_cash_flow_raises_when_dedup_key_field_missing(self):
        self.mock_client.list_records.side_effect = Exception('FieldNameNotFound')

        cf = _completed_cash_flow(amount=100000)

        with pytest.raises(ValueError, match='缺少 dedup_key 字段'):
            self.storage.add_cash_flow(cf)

    def test_add_cash_flow_rejects_transport_model_before_any_write(self):
        transport = CashFlow(
            flow_date=date(2025, 3, 14),
            account='测试账户',
            broker='华泰',
            amount=100,
            currency='CNY',
        )

        with pytest.raises(TypeError, match='CompletedCashFlowFacts'):
            self.storage.add_cash_flow(transport)

        self.mock_client.list_records.assert_not_called()
        self.mock_client.create_record.assert_not_called()

    def test_add_incomplete_foreign_facts_does_not_write_or_update_cache(self):
        complete = _completed_cash_flow(
            amount=10,
            currency='USD',
            exchange_rate=7.2,
        )
        incomplete = replace(
            complete,
            cny_amount=None,
            exchange_rate=None,
        )
        self.storage._cash_flow_agg_loaded_accounts.add('测试账户')
        self.storage._cash_flow_agg_mem_cache['测试账户'] = {
            'cumulative': 100.0,
        }

        with pytest.raises(CashFlowContractError):
            self.storage.add_cash_flow(incomplete)

        self.mock_client.list_records.assert_not_called()
        self.mock_client.create_record.assert_not_called()
        assert self.storage._cash_flow_agg_mem_cache['测试账户'] == {
            'cumulative': 100.0,
        }

    def test_add_invalidates_unloaded_disk_aggregate_instead_of_partial_append(self):
        self.mock_client.list_records.return_value = []
        self.mock_client.create_record.return_value = {
            'record_id': 'cf_rec_123',
            'fields': {},
        }
        self.storage._local_cash_flow_agg_cache.set_account(
            '测试账户',
            {'cumulative': 100.0, 'flow_count': 1},
        )

        self.storage.add_cash_flow(_completed_cash_flow(amount=50))

        assert '测试账户' not in self.storage._cash_flow_agg_loaded_accounts
        assert self.storage._local_cash_flow_agg_cache.get_account('测试账户') == {}

    def test_add_cash_flow_rechecks_stale_dedup_cache_before_replay(self):
        requested = _completed_cash_flow(amount=100)
        changed = _completed_cash_flow(amount=200, record_id='cf_changed')
        matching = requested.with_record_id('cf_matching')
        records = {
            'cf_changed': {
                'record_id': 'cf_changed',
                'fields': changed.to_fields(),
            },
            'cf_matching': {
                'record_id': 'cf_matching',
                'fields': matching.to_fields(),
            },
        }
        self.storage._dedup_key_cache[
            f'cash_flow:{requested.dedup_key}'
        ] = 'cf_changed'
        self.mock_client.get_record_strict.side_effect = (
            lambda _table, record_id: records[record_id]
        )
        self.mock_client.list_records.return_value = [records['cf_matching']]

        result = self.storage.add_cash_flow(requested)

        assert result.record_id == 'cf_matching'
        assert result.dedup_key == requested.dedup_key
        assert result.amount == 100.0
        assert result.was_replayed is True
        assert self.storage._dedup_key_cache[
            f'cash_flow:{requested.dedup_key}'
        ] == 'cf_matching'
        self.mock_client.list_records.assert_called_once()
        self.mock_client.create_record.assert_not_called()

    def test_add_cash_flow_creates_after_stale_dedup_cache_has_no_fresh_match(self):
        requested = _completed_cash_flow(amount=100)
        changed = _completed_cash_flow(amount=200, record_id='cf_changed')
        stale_record = {
            'record_id': 'cf_changed',
            'fields': changed.to_fields(),
        }
        self.storage._dedup_key_cache[
            f'cash_flow:{requested.dedup_key}'
        ] = 'cf_changed'
        self.mock_client.get_record_strict.return_value = stale_record
        self.mock_client.list_records.return_value = []
        self.mock_client.create_record.return_value = {
            'record_id': 'cf_created',
            'fields': requested.to_fields(),
        }

        result = self.storage.add_cash_flow(requested)

        assert result.record_id == 'cf_created'
        assert result.dedup_key == requested.dedup_key
        assert result.was_replayed is False
        self.mock_client.list_records.assert_called_once()
        self.mock_client.create_record.assert_called_once()

    def test_add_cash_flow_hot_cache_uses_decimal_aggregate_semantics(self):
        self.mock_client.list_records.return_value = []
        self.mock_client.create_record.return_value = {
            'record_id': 'cf_created',
            'fields': {},
        }
        cache_payload = {
            'account': '测试账户',
            'daily': {'2025-03-14': 0.1},
            'monthly': {'2025-03': 0.1},
            'yearly': {'2025': 0.1},
            'cumulative': 0.1,
            'flow_count': 1,
            'flows': [],
        }
        self.storage._cash_flow_agg_loaded_accounts.add('测试账户')
        self.storage._cash_flow_agg_mem_cache['测试账户'] = cache_payload
        self.storage._local_cash_flow_agg_cache.set_account(
            '测试账户',
            cache_payload,
        )

        self.storage.add_cash_flow(_completed_cash_flow(amount='0.20'))

        aggregate = self.storage.get_cash_flow_aggs('测试账户')
        assert aggregate['daily']['2025-03-14'] == 0.3
        assert aggregate['monthly']['2025-03'] == 0.3
        assert aggregate['yearly']['2025'] == 0.3
        assert aggregate['cumulative'] == 0.3

    def test_get_cash_flow(self):
        """测试获取单条出入金记录"""
        self.mock_client.get_record_strict.return_value = {
            'record_id': 'cf_rec',
            'fields': {
                'flow_date': '2025-03-14',
                'amount': '100000',
            }
        }

        result = self.storage.get_cash_flow('cf_rec')

        assert result is not None
        assert result.amount == 100000.0
        assert result.currency is None
        assert result.flow_type is None
        assert result.dedup_key is None
        assert result.source is None

    def test_get_cash_flow_preserves_observed_dedup_and_digest_fields(self):
        facts = _completed_cash_flow(
            amount=100,
            source='bank-import',
            record_id='cf_rec',
        )
        fields = facts.to_fields()
        fields['remark'] = 'observed memo'
        fields['updated_at'] = '2025-03-14 09:30:00'
        self.mock_client.get_record_strict.return_value = {
            'record_id': 'cf_rec',
            'fields': fields,
        }

        result = self.storage.get_cash_flow('cf_rec')

        assert result.dedup_key == facts.dedup_key
        assert result.source == 'bank-import'
        assert result.remark == 'observed memo'
        assert result.updated_at == '2025-03-14 09:30:00'

    def test_get_cash_flows(self):
        """测试获取出入金记录列表"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'cf_1',
                'fields': {
                    'flow_date': '2025-03-14',
                    'account': '测试账户',
                    'broker': '华泰',
                    'amount': '100000',
                    'cny_amount': '100000',
                    'currency': 'CNY'
                }
            },
            {
                'record_id': 'cf_2',
                'fields': {
                    'flow_date': '2025-03-13',
                    'account': '测试账户',
                    'broker': '华泰',
                    'amount': '-50000',
                    'cny_amount': '-50000',
                    'currency': 'CNY'
                }
            }
        ]

        flows = self.storage.get_cash_flows(
            account='测试账户',
            start_date=date(2025, 3, 1),
            end_date=date(2025, 3, 14)
        )

        assert len(flows) == 2
        projection = self.mock_client.list_records.call_args.kwargs['field_names']
        assert projection == self.storage.CASH_FLOW_PROJECTION_FIELDS
        assert {'remark', 'source', 'dedup_key'} <= set(projection)

    def test_get_total_cash_flow_cny(self):
        """测试获取累计出入金总额"""
        facts = [
            _completed_cash_flow(amount=100000, record_id='cf_1'),
            _completed_cash_flow(
                amount=-30000,
                flow_date=date(2025, 3, 13),
                record_id='cf_2',
            ),
            _completed_cash_flow(
                amount=50000,
                flow_date=date(2025, 3, 12),
                record_id='cf_3',
            ),
        ]
        self.mock_client.list_records.return_value = [
            {'record_id': item.record_id, 'fields': item.to_fields()}
            for item in facts
        ]

        total = self.storage.get_total_cash_flow_cny('测试账户')

        assert total == 120000.0  # 100000 - 30000 + 50000

    def test_preload_cash_flow_aggs_rejects_foreign_without_cny_amount(self):
        """外币现金流未补人民币金额时，聚合不能静默按原币金额计算。"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'cf_usd',
                'fields': {
                    'flow_date': '2025-03-14',
                    'account': '测试账户',
                    'broker': '华泰',
                    'amount': '10',
                    'currency': 'USD',
                    'flow_type': 'DEPOSIT',
                    'dedup_key': _completed_cash_flow(
                        amount=10,
                        currency='USD',
                    ).dedup_key,
                    'source': 'test',
                },
            }
        ]

        with pytest.raises(CashFlowContractError) as exc_info:
            self.storage.preload_cash_flow_aggs('测试账户', force_refresh=True)

        assert {issue.reason_code for issue in exc_info.value.issues} >= {
            'EXCHANGE_RATE_MISSING',
            'CNY_AMOUNT_MISSING',
        }

    def test_preload_cash_flow_aggs_missing_date_blocks_and_keeps_old_cache(self):
        self.storage._cash_flow_agg_mem_cache['测试账户'] = {
            'cumulative': 42.0,
        }
        self.mock_client.list_records.return_value = [{
            'record_id': 'cf_missing_date',
            'fields': {
                'account': '测试账户',
                'broker': '华泰',
                'amount': 100,
                'currency': 'CNY',
                'flow_type': 'DEPOSIT',
                'cny_amount': 100,
                'exchange_rate': 1,
                'dedup_key': 'untrusted-without-date',
                'source': 'test',
            },
        }]

        with pytest.raises(CashFlowContractError) as exc_info:
            self.storage.preload_cash_flow_aggs(
                '测试账户',
                force_refresh=True,
            )

        assert 'FLOW_DATE_MISSING' in {
            issue.reason_code for issue in exc_info.value.issues
        }
        assert self.storage._cash_flow_agg_mem_cache['测试账户'] == {
            'cumulative': 42.0,
        }

    def test_delete_cash_flow_by_record_id(self):
        """测试通过记录ID删除出入金"""
        self.mock_client.get_record_strict.return_value = {
            'record_id': 'cf_rec',
            'fields': {'account': '测试账户'},
        }
        self.mock_client.delete_record.return_value = True
        self.storage._cash_flow_agg_loaded_accounts.add('测试账户')
        self.storage._cash_flow_agg_mem_cache['测试账户'] = {
            'cumulative': 100.0,
        }
        self.storage._local_cash_flow_agg_cache.set_account(
            '测试账户',
            {'cumulative': 100.0},
        )

        result = self.storage.delete_cash_flow_by_record_id('cf_rec')

        assert result == True
        assert '测试账户' not in self.storage._cash_flow_agg_loaded_accounts
        assert '测试账户' not in self.storage._cash_flow_agg_mem_cache
        assert self.storage._local_cash_flow_agg_cache.get_account('测试账户') == {}

    def test_reconcile_cash_flows_dry_run_fills_manual_cny_row(self):
        """手工现金流只填人工字段时，reconcile dry-run 应补齐系统字段预览。"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'cf_1',
                'fields': {
                    'flow_date': '2025-03-14',
                    'account': '测试账户',
                    'broker': '华泰',
                    'amount': '100000',
                    'currency': 'CNY',
                    'remark': 'manual row',
                },
            }
        ]

        result = self.storage.reconcile_cash_flows(account='测试账户', dry_run=True)

        expected_key = make_cf_dedup_key(CashFlow(
            flow_date=date(2025, 3, 14),
            account='测试账户',
            broker='华泰',
            amount=100000,
            currency='CNY',
            cny_amount=100000,
            exchange_rate=1,
            flow_type='DEPOSIT',
            source='manual',
            remark='manual row',
        ))
        assert result['success'] is True
        assert result['dry_run'] is True
        assert result['change_count'] == 1
        assert result['updated_count'] == 0
        assert result['rows'][0]['updates'] == {
            'flow_type': 'DEPOSIT',
            'exchange_rate': 1.0,
            'cny_amount': 100000.0,
            'dedup_key': expected_key,
            'source': 'manual',
        }
        self.mock_client.batch_update_records.assert_not_called()
        self.mock_client.list_records.assert_called_once()
        assert self.mock_client.list_records.call_args.kwargs['field_names'] == self.storage.CASH_FLOW_RECONCILE_FIELDS
        assert not {
            'exchange_rate_date',
            'exchange_rate_source',
            'exchange_rate_evidence_type',
        } & set(self.storage.CASH_FLOW_RECONCILE_FIELDS)

    def test_reconcile_cash_flows_falls_back_when_updated_at_missing(self):
        """live cash_flow 表没有 updated_at 时，reconcile 应降级投影字段继续读。"""
        self.mock_client.list_records.side_effect = [
            Exception('FieldNameNotFound'),
            [
                {
                    'record_id': 'cf_1',
                    'fields': {
                        'flow_date': '2025-03-14',
                        'account': '测试账户',
                        'broker': '华泰',
                        'amount': '100000',
                        'currency': 'CNY',
                    },
                }
            ],
        ]

        result = self.storage.reconcile_cash_flows(account='测试账户', dry_run=True)

        assert result['change_count'] == 1
        assert self.mock_client.list_records.call_count == 2
        fallback_fields = self.mock_client.list_records.call_args.kwargs['field_names']
        assert 'updated_at' not in fallback_fields

    def test_reconcile_cash_flows_uses_fx_rates_for_foreign_manual_row(self):
        """外币手工现金流缺人民币金额时，用注入汇率补齐。"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'cf_usd',
                'fields': {
                    'flow_date': '2025-03-14',
                    'account': '测试账户',
                    'broker': '华泰',
                    'amount': '10',
                    'currency': 'USD',
                },
            }
        ]

        result = self.storage.reconcile_cash_flows(
            account='测试账户',
            dry_run=True,
            fx_rates={'USDCNY': 7.2},
        )

        updates = result['rows'][0]['updates']
        assert updates['exchange_rate'] == 7.2
        assert updates['cny_amount'] == 72.0
        assert updates['flow_type'] == 'DEPOSIT'
        assert result['rows'][0]['fx_evidence'] == {
            'exchange_rate_date': '2025-03-14',
            'exchange_rate_source': 'injected_historical_rate',
            'exchange_rate_evidence_type': 'provider',
        }
        assert not {
            'exchange_rate_date',
            'exchange_rate_source',
            'exchange_rate_evidence_type',
        } & set(updates)

    def test_reconcile_cash_flows_refuses_repository_level_provider_apply(self):
        with pytest.raises(
            ValueError,
            match="provider FX apply requires the application confirmation workflow",
        ):
            self.storage.reconcile_cash_flows(
                account="测试账户",
                dry_run=False,
                fx_rates={"USDCNY": {"rate": 7.2, "date": "2025-03-14", "source": "test"}},
            )
        self.mock_client.batch_update_records.assert_not_called()

    def test_reconcile_cash_flows_recomputes_system_fields_after_manual_amount_edit(self):
        """已补齐行被手工改 amount 后，reconcile 应保留汇率并重算系统字段。"""
        stale_key = make_cf_dedup_key(CashFlow(
            flow_date=date(2025, 3, 14),
            account='测试账户',
            broker='华泰',
            amount=5,
            currency='USD',
            cny_amount=36,
            exchange_rate=7.2,
            flow_type='DEPOSIT',
        ))
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'cf_usd',
                'fields': {
                    'flow_date': '2025-03-14',
                    'account': '测试账户',
                    'broker': '华泰',
                    'amount': '10',
                    'currency': 'USD',
                    'exchange_rate': '7.2',
                    'exchange_rate_date': '2025-03-14',
                    'exchange_rate_source': 'historical-provider',
                    'exchange_rate_evidence_type': 'provider',
                    'cny_amount': '36',
                    'flow_type': 'WITHDRAW',
                    'dedup_key': stale_key,
                    'source': 'manual',
                },
            }
        ]

        result = self.storage.reconcile_cash_flows(account='测试账户', dry_run=True)

        updates = result['rows'][0]['updates']
        assert updates['flow_type'] == 'DEPOSIT'
        assert updates['cny_amount'] == 72.0
        assert updates['dedup_key'] != stale_key
        assert 'exchange_rate' not in updates

    def test_reconcile_cash_flows_apply_updates_and_invalidates_cache(self):
        """apply 写回飞书后，应失效现金流聚合缓存，避免净值继续读旧值。"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'cf_1',
                'fields': {
                    'flow_date': '2025-03-14',
                    'account': '测试账户',
                    'broker': '华泰',
                    'amount': '-100',
                    'currency': 'CNY',
                },
            }
        ]
        self.mock_client.batch_update_records.return_value = [{'record_id': 'cf_1', 'fields': {}}]
        self.storage._cash_flow_agg_loaded_accounts.add('测试账户')
        self.storage._cash_flow_agg_mem_cache['测试账户'] = {'cumulative': 1.0}
        self.storage._local_cash_flow_agg_cache.set_account('测试账户', {'cumulative': 1.0}, _flush=True)

        result = self.storage.reconcile_cash_flows(account='测试账户', dry_run=False)

        assert result['updated_count'] == 1
        self.mock_client.batch_update_records.assert_called_once()
        table, payload = self.mock_client.batch_update_records.call_args.args
        assert table == 'cash_flow'
        assert payload[0]['record_id'] == 'cf_1'
        assert payload[0]['fields']['flow_type'] == 'WITHDRAW'
        assert payload[0]['fields']['cny_amount'] == -100.0
        assert '测试账户' not in self.storage._cash_flow_agg_loaded_accounts
        assert '测试账户' not in self.storage._cash_flow_agg_mem_cache
        assert self.storage._local_cash_flow_agg_cache.get_account('测试账户') == {}


class TestFeishuStorageNAVOperations:
    """测试飞书存储层净值操作"""

    def setup_method(self):
        self.mock_client = Mock()
        self.storage = FeishuStorage(client=self.mock_client)

    def test_write_nav_record_create(self):
        """测试写入新净值记录"""
        self.mock_client.list_records.return_value = []  # 不存在
        self.mock_client.create_record.return_value = {
            'record_id': 'nav_rec_123',
            'fields': {}
        }

        nav = NAVHistory(
            date=date(2025, 3, 14),
            account='测试账户',
            total_value=1000000.0,
            cash_value=100000.0,
            stock_value=900000.0,
            shares=1000000.0,
            nav=1.0
        )

        self.storage.write_nav_record(nav)

        assert nav.record_id == 'nav_rec_123'
        self.mock_client.create_record.assert_called_once()

    def test_write_nav_record_update(self):
        """测试更新现有净值记录"""
        self.mock_client.list_records.return_value = [{
            'record_id': 'existing_nav',
            'fields': {
                'date': '2025-03-14',
                'account': '测试账户',
                'nav': '0.95',
            }
        }]
        self.mock_client.update_record.return_value = {
            'record_id': 'existing_nav',
            'fields': {}
        }

        nav = NAVHistory(
            date=date(2025, 3, 14),
            account='测试账户',
            total_value=1000000.0,
            shares=1000000.0,
            nav=1.0
        )

        self.storage.write_nav_record(nav, overwrite_existing=True)

        assert nav.record_id == 'existing_nav'
        self.mock_client.update_record.assert_called_once()

    def test_get_nav_history(self):
        """测试获取净值历史"""
        from datetime import timedelta
        today = date.today()
        yesterday = today - timedelta(days=1)
        day_before = today - timedelta(days=2)

        self.mock_client.list_records.return_value = [
            {
                'record_id': 'nav_1',
                'fields': {
                    'date': today.isoformat(),
                    'account': '测试账户',
                    'total_value': '1000000',
                    'nav': '1.0'
                }
            },
            {
                'record_id': 'nav_2',
                'fields': {
                    'date': yesterday.isoformat(),
                    'account': '测试账户',
                    'total_value': '990000',
                    'nav': '0.99'
                }
            }
        ]

        navs = self.storage.get_nav_history('测试账户', days=30)

        assert len(navs) == 2
        # 按日期正序排列
        assert navs[0].date == yesterday
        assert navs[1].date == today

    def test_get_latest_nav(self):
        """测试获取最新净值"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'nav_1',
                'fields': {
                    'date': '2025-03-13',
                    'account': '测试账户',
                    'nav': '0.99',
                }
            },
            {
                'record_id': 'nav_2',
                'fields': {
                    'date': '2025-03-14',
                    'account': '测试账户',
                    'nav': '1.0',
                }
            },
            {
                'record_id': 'nav_3',
                'fields': {
                    'date': '2025-03-12',
                    'account': '测试账户',
                    'nav': '0.98',
                }
            }
        ]

        result = self.storage.get_latest_nav('测试账户')

        assert result is not None
        assert result.date == date(2025, 3, 14)
        assert result.nav == 1.0

    def test_get_nav_on_date(self):
        """测试获取指定日期的净值"""
        self.mock_client.list_records.return_value = [{
            'record_id': 'nav_1',
            'fields': {
                'date': '2025-03-14',
                'account': '测试账户',
                'nav': '1.0',
            }
        }]

        result = self.storage.get_nav_on_date('测试账户', date(2025, 3, 14))

        assert result is not None
        assert result.date == date(2025, 3, 14)

    def test_get_latest_nav_before(self):
        """测试获取指定日期前的最新净值"""
        self.mock_client.list_records.return_value = [
            {
                'record_id': 'nav_1',
                'fields': {
                    'date': '2025-03-12',
                    'account': '测试账户',
                    'nav': '0.98',
                }
            },
            {
                'record_id': 'nav_2',
                'fields': {
                    'date': '2025-03-13',
                    'account': '测试账户',
                    'nav': '0.99',
                }
            }
        ]

        result = self.storage.get_latest_nav_before('测试账户', date(2025, 3, 14))

        assert result is not None
        assert result.date == date(2025, 3, 13)

    def test_get_total_shares(self):
        """测试获取总份额"""
        self.mock_client.list_records.return_value = [{
            'record_id': 'nav_1',
            'fields': {
                'date': '2025-03-14',
                'account': '测试账户',
                'shares': '1000000',
                'nav': '1.0',
            }
        }]

        shares = self.storage.get_total_shares('测试账户')

        assert shares == 1000000.0

    def test_delete_nav_by_record_id(self):
        """测试通过记录ID删除净值"""
        self.mock_client.delete_record.return_value = True

        result = self.storage.delete_nav_by_record_id('nav_rec')

        assert result == True


class TestFeishuStoragePriceOperations:
    """测试价格缓存操作（已迁移到本地文件缓存）"""

    def setup_method(self):
        self.mock_client = Mock()
        self.storage = FeishuStorage(client=self.mock_client)
        self.mock_local_cache = Mock()
        self.storage._local_price_cache = self.mock_local_cache

    def test_get_price_valid(self):
        """测试获取有效价格缓存"""
        self.mock_local_cache.get.return_value = PriceCache(
            asset_id='000001',
            asset_name='平安银行',
            asset_type=AssetType.A_STOCK,
            price=10.5,
            currency='CNY',
            cny_price=10.5
        )

        result = self.storage.get_price('000001')

        assert result is not None
        assert result.price == 10.5
        self.mock_local_cache.get.assert_called_once_with('000001')

    def test_get_price_expired(self):
        """测试获取过期价格缓存返回None"""
        self.mock_local_cache.get.return_value = None

        result = self.storage.get_price('000001')

        assert result is None

    def test_get_price_not_found(self):
        """测试价格缓存不存在"""
        self.mock_local_cache.get.return_value = None

        result = self.storage.get_price('999999')

        assert result is None

    def test_save_price_create(self):
        """测试保存价格缓存"""
        price = PriceCache(
            asset_id='000001',
            asset_name='平安银行',
            asset_type=AssetType.A_STOCK,
            price=10.5,
            currency='CNY',
            cny_price=10.5
        )

        self.storage.save_price(price)

        self.mock_local_cache.save.assert_called_once_with(price)

    def test_save_price_update(self):
        """测试更新价格缓存"""
        price = PriceCache(
            asset_id='000001',
            asset_type=AssetType.A_STOCK,
            price=10.5,
            currency='CNY',
            cny_price=10.5
        )

        self.storage.save_price(price)

        self.mock_local_cache.save.assert_called_once_with(price)

    def test_get_all_prices(self):
        """测试获取所有有效价格缓存"""
        self.mock_local_cache.get_all.return_value = [
            PriceCache(asset_id='000001', price=10.5, currency='CNY', cny_price=10.5),
            PriceCache(asset_id='00700', price=400.0, currency='HKD', cny_price=400.0),
        ]

        prices = self.storage.get_all_prices()

        assert len(prices) == 2
        self.mock_local_cache.get_all.assert_called_once()

    def test_get_all_prices_filter_expired(self):
        """测试本地缓存自动过滤过期价格"""
        self.mock_local_cache.get_all.return_value = [
            PriceCache(asset_id='000001', price=10.5, currency='CNY', cny_price=10.5),
        ]

        prices = self.storage.get_all_prices()

        assert len(prices) == 1
        assert prices[0].asset_id == '000001'


def test_cash_flow_replay_marker_is_runtime_only():
    client = Mock()
    storage = FeishuStorage(client=client)
    client.list_records.return_value = [{"record_id": "cf-existing", "fields": {}}]
    facts = _completed_cash_flow(
        account="a",
        broker="某券商",
        amount=100,
        source="test",
        record_id="cf-existing",
    )
    client.get_record_strict.return_value = {
        "record_id": "cf-existing",
        "fields": facts.to_fields(),
    }
    cf = _completed_cash_flow(
        account="a",
        broker="某券商",
        amount=100,
        source="test",
    )

    result = storage.add_cash_flow(cf)

    assert result.was_replayed is True
    assert "was_replayed" not in result.model_dump()
    assert "_was_replayed" not in result.model_dump()
    client.create_record.assert_not_called()
