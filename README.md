# shiftcomply

排班合规引擎库（纯标准库，SQLite 持久化），供医院、消防等单位的排班系统调用。

## 快速上手

```python
import datetime as dt
from shiftcomply import Engine, Store

store = Store("schedule.db")          # 或 Store() 使用内存库
dept = store.add_department("ER", "America/New_York")   # IANA 时区
emp = store.add_employee(dept, "Alice")
store.add_certificate(emp, "ACLS", dt.date(2027, 1, 1))  # UTC 到期日

tmpl = store.add_template(dept, "7:00 PM", "7:00 AM", 2, ("ACLS",))
shift = store.create_shift(tmpl, dt.date(2026, 11, 2))  # 部门本地日期

eng = Engine(store)
r = eng.assign(emp, shift, actor="sup-1")
r.ok            # False 时状态不变，r.violations 含全部涉及违规
r.warnings      # 通过时的证书预警（CERT_EXPIRING）

eng.unassign(emp, shift, actor="sup-1")                 # 不做校验
eng.swap(a, shift_a, b, shift_b, actor="sup-1")         # 原子互换
```

- 12 小时制时刻（`"7:00 PM"`、`"12:30 AM"`）在装载时校验，`"13:00 PM"`、
  `"0:15 AM"`、缺 AM/PM 等非法输入直接抛出 `ClockTimeError`。
- 违规（OVERLAP / REST / MAX_24H / MAX_WEEK / CERT / OVERSTAFF）连同 UTC
  时间戳与 actor 追加进 `compliance_log` 表（只追加、不改不删），
  可通过 `store.log_entries()` 读取。

## 规则要点

- 同一员工班次不得重叠；相邻工作间隔 <10h 违规（间隔为 0 算连续工作）。
- 任意滚动 24h 窗口工作 ≤16h（按真实流逝时间，部分重叠按重叠计）。
- 部门本地时间周一 0 点起每周 ≤60h，跨周班次按时刻拆分。
- 所需证书须覆盖整段班次（到期日当天 UTC 结束失效），
  失效时刻距班次开始不足 30 天仅预警。
- 分配人数不得超过模板编制。
- 时长一律按 UTC 真实流逝计算；不存在的本地时刻按跳变前偏移换算，
  重复的本地时刻取较早那次（fold=0）。

## 测试

```
python3 -m unittest discover -s tests
```
