# 排班合规引擎（scheduling）

仅使用 Python 标准库（`sqlite3` 持久化、`zoneinfo` 处理时区），供医院、消防等单位
的排班系统调用。

## 数据模型

- `Department(id, timezone)`：`timezone` 为 IANA 名称（如 `America/New_York`）。
- `Employee(id, department_id, certificates)`：证书为 `(标签, UTC到期日)`。
- `ShiftTemplate(id, department_id, start_text, end_text, headcount, required_certificates)`：
  起止时刻为 12 小时制文本（如 `7:00 PM`、`12:30 AM`）。非法输入（`13:00 PM`、
  `0:15 AM`、缺 AM/PM 等）在装载时抛 `ValueError`。
- `ShiftInstance(template_id, local_date)`：模板 + 部门本地日期，班次 ID 形如
  `T@2026-11-01`。

结束时刻不晚于开始时刻即跨到次日。不存在的本地时刻按跳变前偏移换算，重复的本地
时刻取较早那次（PEP 495 `fold=0`）；所有时长按真实流逝时间计算。

## 操作（均带 actor）

```python
from datetime import date
from scheduling import Store, ComplianceEngine, Department, Employee, Certificate, ShiftTemplate, ShiftInstance
from scheduling.store import shift_instance_id

store = Store("scheduling.db")          # 或 ":memory:"
engine = ComplianceEngine(store)

store.add_department(Department("h", "America/New_York"))
store.add_employee(Employee("e1", "h", (Certificate("BLS", date(2030, 1, 1)),)))
store.add_shift_template(ShiftTemplate("N", "h", "11:00 PM", "7:00 AM", 2, ("BLS",)))
sid = store.add_shift_instance(ShiftInstance("N", date(2026, 11, 1)))
sid = shift_instance_id("N", date(2026, 11, 1))      # -> "N@2026-11-01"

engine.assign("supervisor", "e1", sid)    # 按变更后状态校验
engine.unassign("supervisor", "e1", sid)  # 不校验
engine.swap("supervisor", "e1", s1, "e2", s2)  # 对交换后状态一次性判定
```

返回 `OperationResult(ok, violations, warnings)`。拒绝时整笔回滚、状态不变；
每条违规（类型、员工、相关班次）连同 UTC 时间戳与 actor 追加进只追加的
`compliance_log` 表（带 UPDATE/DELETE 拒绝触发器）。通过时 `warnings`
返回距班次开始不足 30 天到期的证书预警。

## 规则

- 同一员工班次不得重叠；相邻班次至少休息 10 小时（间隔为 0 算连续工作）。
- 任意连续 24 小时内工作不超过 16 小时，部分重叠按重叠部分计（枚举
  班次结束与“班次开始+24h”断点窗口，不按自然日近似）。
- 以部门本地周一 00:00 为界，每周不超过 60 小时；跨周班次按时刻拆分。
- 班次所需证书必须覆盖整段；到期日当天 UTC 结束（次日 00:00 UTC）前有效，
  距班次开始不足 30 天仅预警。
- 分配人数不得超过编制。
- swap 作为单组增删一次性判定并在一个事务内原子生效。

## 测试

```bash
python3 -m unittest discover -s tests
```
