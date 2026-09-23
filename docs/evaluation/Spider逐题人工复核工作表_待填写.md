# Spider 回答人工复核工作表

> 未标注：此表由程序根据已有结果生成，不代表人工已完成审核，也不产生人工准确率。

先对照问题、完整参考结果及原查询明细，独立判断最终回答是否正确、完整、有证据；必要时运行 SQL 或核查数据库。
每题填写审核人、日期、三维标签（通过/不通过/无法判断）和具体理由。此表不展示自动评分；完成标注后再对照评分，减少先入为主。

## 题目 47 · pets_1

**问题：** Find the weight of the youngest dog.

**参考 SQL：**

```sql
SELECT weight FROM pets ORDER BY pet_age LIMIT 1
```

**完整参考结果：**

```json
[
  [
    9.3
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT * FROM pets ORDER BY pet_age LIMIT 500
```

**Agent 最终回答：**

**结果：最年轻的狗体重为 9.3（重量单位未在数据中标注）。**

统计口径：
- 来自 `pets` 表（仅 3 行，为全量数据，未截断）；
- 按 `PetType = 'dog'` 过滤，共 2 只狗：PetID 2003（1 岁，9.3）、PetID 2002（2 岁，13.4）；
- 最年轻者按 `pet_age` 最小值取，即 1 岁的 PetID 2003，其 `weight = 9.3`。

不确定性：数据未标明体重单位（千克/磅）；若存在同龄并列最小的情况，样本中未出现。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 60 · pets_1

**问题：** What are the students' first names who have both cats and dogs as pets?

**参考 SQL：**

```sql
SELECT T1.Fname FROM student AS T1 JOIN has_pet AS T2 ON T1.stuid  =  T2.stuid JOIN pets AS T3 ON T3.petid  =  T2.petid WHERE T3.pettype  =  'cat' INTERSECT SELECT T1.Fname FROM student AS T1 JOIN has_pet AS T2 ON T1.stuid  =  T2.stuid JOIN pets AS T3 ON T3.petid  =  T2.petid WHERE T3.pettype  =  'dog'
```

**完整参考结果：**

```json
[]
```

**Agent 主要 SQL：**

```sql
SELECT DISTINCT s.Fname, s.LName FROM student AS s JOIN has_pet AS h ON h.StuID = s.StuID JOIN pets AS p ON p.PetID = h.PetID WHERE p.PetType IN ('cat', 'dog') GROUP BY s.StuID, s.Fname, s.LName HAVING COUNT(DISTINCT p.PetType) = 2 ORDER BY s.Fname LIMIT 500
```

**Agent 最终回答：**

**结果：没有符合条件的学生，查询返回 0 行。**

统计口径：把 `has_pet` 和 `pets` 表关联，筛选 `PetType` 为 'cat' 或 'dog' 的记录，按学生分组后要求这两种类型都出现过（`COUNT(DISTINCT PetType) = 2`）。

数据实际情况如下：
- 宠物表共 3 条记录：1 只猫（PetID 2001）和 2 只狗（2002、2003）
- 领养表中只有 2 名学生：StuID 1001 只养猫，StuID 1002 只养狗（两只）

所以不存在同时养猫和狗的学生。需要注意：该数据集很小（`has_pet` 仅 3 行，`pets` 仅 3 行），结论只适用于现有数据；另外 `PetType` 取值已确认为小写 'cat'/'dog'，若有其他拼写变体不会被匹配到。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 71 · pets_1

**问题：** Find the average and maximum age for each type of pet.

**参考 SQL：**

```sql
SELECT avg(pet_age) ,  max(pet_age) ,  pettype FROM pets GROUP BY pettype
```

**完整参考结果：**

```json
[
  [
    3.0,
    3,
    "cat"
  ],
  [
    1.5,
    2,
    "dog"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT PetType, AVG(pet_age) AS avg_age, MAX(pet_age) AS max_age, COUNT(*) AS pet_count FROM pets GROUP BY PetType ORDER BY PetType LIMIT 500
```

**Agent 最终回答：**

按宠物类型统计的年龄结果如下（数据来自 `pets` 表的 `pet_age` 字段，按 `PetType` 分组）：

| 宠物类型 | 平均年龄 | 最大年龄 | 宠物数量 |
|---|---|---|---|
| cat | 3.0 | 3 | 1 |
| dog | 1.5 | 2 | 2 |

说明与不确定性：
- 全表仅 3 条宠物记录（cat 1 只、dog 2 只），样本极小，平均值几乎没有统计代表性。
- `pets` 表共 3 行，与 `has_pet` 表（3 行）行数一致，但这里未做去重或与其他表关联；若“平均年龄”需按拥有宠物的学生统计或多宠情况需去重，口径会不同。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 79 · pets_1

**问题：** Find the number of pets for each student who has any pet and student id.

**参考 SQL：**

```sql
SELECT count(*) ,  T1.stuid FROM student AS T1 JOIN has_pet AS T2 ON T1.stuid  =  T2.stuid GROUP BY T1.stuid
```

**完整参考结果：**

```json
[
  [
    1,
    1001
  ],
  [
    2,
    1002
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT h.StuID, COUNT(p.PetID) AS pet_count FROM has_pet AS h JOIN pets AS p ON h.PetID = p.PetID GROUP BY h.StuID ORDER BY h.StuID LIMIT 500
```

**Agent 最终回答：**

结果（仅列出至少有一只宠物的学生）：

| StuID | 宠物数量 |
|---|---|
| 1001 | 1 |
| 1002 | 2 |

统计口径与说明：
- 以 `has_pet`（学生-宠物关联表）按 `StuID` 分组，统计关联记录数；并内连接 `pets` 表以确保宠物在 `pets` 表中真实存在（三只 PetID 2001–2003 均能匹配，无丢失）。
- `has_pet` 共 3 行、无重复行、无空值，包含 2 名学生（1001、1002）和 3 只宠物。
- 未出现在结果中的其他 32 名学生没有任何宠物记录，故不在结果内（不是数值 0 的推断，而是无关联记录）。
- 由于 `has_pet` 中没有重复行，此处「宠物数量」即去重宠物数，行数与去重数一致，不存在口径歧义。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 111 · car_1

**问题：** What is the accelerate of the car make amc hornet sportabout (sw)?

**参考 SQL：**

```sql
SELECT T1.Accelerate FROM CARS_DATA AS T1 JOIN CAR_NAMES AS T2 ON T1.Id  =  T2.MakeId WHERE T2.Make  =  'amc hornet sportabout (sw)';
```

**完整参考结果：**

```json
[
  [
    13.5
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT n.Make, d.Accelerate, d.Year, d.Cylinders FROM car_names AS n JOIN cars_data AS d ON n.MakeId = d.Id WHERE LOWER(n.Make) LIKE '%amc hornet sportabout%' LIMIT 500
```

**Agent 最终回答：**

**amc hornet sportabout (sw)** 的 Accelerate（加速性能指标）为 **13.5**。

- 统计口径：按车型全名精确匹配 `car_names.Make`，再关联 `cars_data` 中同名记录。
- 该车型仅 1 条匹配记录（1971 年，6 缸），无重复或歧义。
- 注：数据集中未提供 Accelerate 的单位与定义说明；按经典汽车数据集惯例，它通常指 0–60 mph 加速时间（秒），但此处无法从数据内确认。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 213 · flight_2

**问题：** How many flights does airline 'JetBlue Airways' have?

**参考 SQL：**

```sql
SELECT count(*) FROM FLIGHTS AS T1 JOIN AIRLINES AS T2 ON T1.Airline  =  T2.uid WHERE T2.Airline = "JetBlue Airways"
```

**完整参考结果：**

```json
[
  [
    100
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT a.Airline, a.uid, COUNT(*) AS flight_count, COUNT(DISTINCT f.FlightNo) AS distinct_flight_no FROM flights AS f JOIN airlines AS a ON f.Airline = a.uid WHERE a.Airline = 'JetBlue Airways' GROUP BY a.Airline, a.uid LIMIT 500
```

**Agent 最终回答：**

JetBlue Airways（airlines.uid = 8）在 flights 表中记录有 **100 条航班**。

统计口径：
- 表结构：`flights.Airline` 是 BIGINT，需与 `airlines.uid` 关联才能匹配航空公司名称。
- 指标：按关联后 `COUNT(*)` 计记录行数；因为 `COUNT(DISTINCT FlightNo)` 同为 100，说明该航司的航班号没有重复，两种计数方式结果一致。
- 数据范围：仅限当前数据集中 flights 表的 1200 行记录，非实际运营航班总量。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 345 · cre_Doc_Template_Mgt

**问题：** Show all template type codes that are not used by any document.

**参考 SQL：**

```sql
SELECT template_type_code FROM Templates EXCEPT SELECT template_type_code FROM Templates AS T1 JOIN Documents AS T2 ON T1.template_id  =  T2.template_id
```

**完整参考结果：**

```json
[
  [
    "CV"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT * FROM templates ORDER BY Template_Type_Code, Template_ID LIMIT 500
```

**Agent 最终回答：**

**未能完成最终判定（SQL 调用次数已达上限）。** 以下是已确认的事实，以及唯一能说明的“确定性缺口”：

已确认的结构与数据
- `ref_template_types`（5 个类型）：AD=Advertisement、BK=Book、CV=CV、PP=Paper、PPT=Presentation
- `templates`：共 **20 行**（20 个不同 Template_ID），但预览只返回了前 15 行，**还有 5 个模板（含被文档引用的 Template_ID 6、7、10、25 等）的类型未看到**
- `documents`：15 行；其 `Template_ID` 为 7, 25, 6, 20, 14, 11, 10, 1, 22, 21, 4, 8

从已见数据可推断（不可作为结论）
- 已见并已被文档使用的类型：**AD、BK、PP**
- 已见的 CV 模板（Template_ID 15、16）**没有出现在文档引用中**，但 CV 是否存在未见的模板被引用，无法确认
- **PPT 在已见的 15 个模板中完全不存在**，但同样无法排除它在那 5 个未见模板中

结论
无法给出完整、可靠的“未被任何文档使用的模板类型代码”清单。要给出准确答案，需要执行：

```sql
SELECT r.Template_Type_Code, r.Template_Type_Description
FROM ref_template_types r
WHERE NOT EXISTS (
  SELECT 1 FROM templates t
  JOIN documents d ON d.Template_ID = t.Template_ID
  WHERE t.Template_Type_Code = r.Template_Type_Code
);
```

统计口径说明：以“模板类型”为单位，只要该类型下任一模板版本被任一文档引用即算“已使用”；没有任何模板的类型也算“未使用”。请在下一轮允许我继续查询时，我用这条 SQL 给出最终清单。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 402 · course_teach

**问题：** What are the names of the teachers and the courses they teach in ascending alphabetical order by the name of the teacher?

**参考 SQL：**

```sql
SELECT T3.Name ,  T2.Course FROM course_arrange AS T1 JOIN course AS T2 ON T1.Course_ID  =  T2.Course_ID JOIN teacher AS T3 ON T1.Teacher_ID  =  T3.Teacher_ID ORDER BY T3.Name
```

**完整参考结果：**

```json
[
  [
    "Anne Walker",
    "History"
  ],
  [
    "Anne Walker",
    "Bible"
  ],
  [
    "Gustaaf Deloor",
    "Science"
  ],
  [
    "Kearsley Brown",
    "Math"
  ],
  [
    "Lucy Wong",
    "Music"
  ],
  [
    "Vicente Carretero",
    "Math"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT t.Name AS Teacher_Name, c.Course AS Course FROM course_arrange AS ca JOIN teacher AS t ON t.Teacher_ID = ca.Teacher_ID JOIN course AS c ON c.Course_ID = ca.Course_ID ORDER BY t.Name ASC, c.Course ASC LIMIT 500
```

**Agent 最终回答：**

| Teacher_Name | Course |
|---|---|
| Anne Walker | Bible |
| Anne Walker | History |
| Gustaaf Deloor | Science |
| Kearsley Brown | Math |
| Lucy Wong | Music |
| Vicente Carretero | Math |

**统计口径**：将 `course_arrange`（授课安排）分别与 `teacher`（按 Teacher_ID）和 `course`（按 Course_ID）做内连接，取教师姓名 + 课程名，按教师姓名升序、课程名升序排列；每一行代表一条“教师-课程”授课关系（Anne Walker 教两门课，故占两行）。

**说明与不确定性**：
- 结果共 6 条授课关系，涉及 5 位教师；`teacher` 表共 7 人，因此有 2 位教师没有出现在授课安排表中（未被分配课程），未列入结果。如果你希望把这些“暂无课程”的教师也列出（课程为空），可以改用左连接。
- 排序按字符串默认排序（空格、大小写敏感），教师姓名均由首字母大写构成，未出现大小写混排问题。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 495 · battle_death

**问题：** What is the average number of injuries caused each time?

**参考 SQL：**

```sql
SELECT avg(injured) FROM death
```

**完整参考结果：**

```json
[
  [
    1.0769230769230769
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT COUNT(*) AS casualty_records, SUM(injured) AS total_injured, ROUND(AVG(injured), 2) AS avg_injured_per_record, ROUND(AVG(CAST(injured AS DOUBLE)), 4) AS avg_injured_exact FROM death LIMIT 500
```

**Agent 最终回答：**

**结论：按每条伤亡事件记录计算，平均受伤人数约为 1.08 人/次**（总受伤 14 人 ÷ 13 条记录 = 1.0769）。

| 指标 | 数值 |
|---|---|
| 伤亡事件记录数 | 13 |
| 受伤总人数 | 14 |
| 平均每次受伤人数 | 1.08 |

说明与不确定性：
- 上述统计口径为「`death` 表中每一条伤亡记录算作一次事件」。`injured` 字段没有空值，因此无需对缺失值做额外处理。
- 数据分布很不均衡：13 条记录中只有 2 条记录有受伤人数（9 人和 5 人），其余 11 条记录均为 0 人；中位数是 0 人，所以那个「平均数」容易造成误导。
- 如果你说的「每次」是指**每场战役**：只有 8 场战役中的 1 场有受伤记录（Battle of Messinopolis，14 人）；每场平均为 14÷8 = 1.75 人，其中 7 场没有受伤记录。
- 另外提醒：`ship.lost_in_battle` 与 `battle.id` 的对应关系在数据里看起来像是错位的（例如船只 id 4–8 恰好对应战役 id 4–8），因此「按战役」的统计结果可信度较低；如果你需要的是按战役统计，请确认关联方式。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 531 · student_transcripts_tracking

**问题：** What is the description of the department whose name has the substring the computer?

**参考 SQL：**

```sql
SELECT department_description FROM Departments WHERE department_name LIKE '%computer%'
```

**完整参考结果：**

```json
[
  [
    "error"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT department_id, department_name, department_description FROM departments WHERE LOWER(department_name) LIKE '%computer%' OR LOWER(department_name) LIKE '%the computer%' LIMIT 500
```

**Agent 最终回答：**

**结果**

| department_name | department_description |
|---|---|
| computer science | error |

- 我使用了 `departments` 表（15 行），按 `department_name` 做不区分大小写的模糊匹配。
- 说明：如果按字面匹配子串 **"the computer"**，则没有任何记录（0 行）；若按 **"computer"** 匹配，则唯一命中 `computer science`，其描述字段内容为 `error`（该值看起来像数据质量问题，并非真实的业务描述）。
- 因此答案：描述为 **error**，且这是基于 "computer" 子串口径的结果，如果你确实需要字面 "the computer"，则不存在匹配部门。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 546 · student_transcripts_tracking

**问题：** What are the names of all courses that have some students enrolled?

**参考 SQL：**

```sql
SELECT DISTINCT T1.course_name FROM Courses AS T1 JOIN Student_Enrolment_Courses AS T2 ON T1.course_id  =  T2.course_id
```

**完整参考结果：**

```json
[
  [
    "la"
  ],
  [
    "pl"
  ],
  [
    "ai"
  ],
  [
    "math"
  ],
  [
    "ml"
  ],
  [
    "os"
  ],
  [
    "en"
  ],
  [
    "nlp"
  ],
  [
    "rs"
  ],
  [
    "db"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT DISTINCT c.course_id, c.course_name, COUNT(DISTINCT se.student_id) AS enrolled_students FROM courses AS c JOIN student_enrolment_courses AS sec ON sec.course_id = c.course_id JOIN student_enrolment AS se ON se.student_enrolment_id = sec.student_enrolment_id GROUP BY c.course_id, c.course_name ORDER BY c.course_id LIMIT 500
```

**Agent 最终回答：**

**有学生选修的课程共 10 门**（按课程去重，非 15 门全部课程）：

| course_id | 课程名称 | 选课学生数（去重） |
|---|---|---|
| 2 | math | 2 |
| 3 | os | 1 |
| 4 | en | 1 |
| 6 | la | 2 |
| 8 | nlp | 1 |
| 10 | ml | 2 |
| 11 | ai | 1 |
| 12 | db | 1 |
| 13 | rs | 2 |
| 14 | pl | 2 |

统计口径：courses → student_enrolment_courses → student_enrolment 内连接，即课程必须同时有选课记录和对应的学生注册记录；`enrolled_students` 为去重学生数。结果基于返回的全部 10 行（未被截断），因此未列出 id 为 1、5、7、9、15 的课程，可视为无学生选修。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 556 · student_transcripts_tracking

**问题：** What is the first, middle, and last name of the first student to register?

**参考 SQL：**

```sql
SELECT first_name ,  middle_name ,  last_name FROM Students ORDER BY date_first_registered ASC LIMIT 1
```

**完整参考结果：**

```json
[
  [
    "Timmothy",
    "Anna",
    "Ward"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT first_name, middle_name, last_name FROM students ORDER BY date_first_registered ASC, student_id ASC LIMIT 1
```

**Agent 最终回答：**

The first student to register was **Timmothy Anna Ward** — first name: **Timmothy**, middle name: **Anna**, last name: **Ward**.

This is based on the earliest non-null `date_first_registered`; ties would be resolved by the lowest `student_id`.


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 578 · student_transcripts_tracking

**问题：** What are the different addresses that have students living there?

**参考 SQL：**

```sql
SELECT count(DISTINCT current_address_id) FROM Students
```

**完整参考结果：**

```json
[
  [
    9
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT a.address_id, a.line_1, a.line_2, a.city, a.state_province_county, a.zip_postcode, a.country, COUNT(DISTINCT s.student_id) AS student_count FROM addresses AS a JOIN students AS s ON s.current_address_id = a.address_id GROUP BY ALL ORDER BY a.address_id LIMIT 500
```

**Agent 最终回答：**

**有学生居住的地址（按当前居住地址统计）共 9 个，覆盖全部 15 名学生：**

| address_id | 地址 | 城市 / 州 / 国家 | 学生数 |
|---|---|---|---|
| 1 | 2294 Grant Square Apt. 235, Apt. 370 | Port Chelsea / Virginia / Iceland | 1 |
| 2 | 3999 Aufderhar Ways Suite 593, Apt. 388 | Lake Laishafurt / Kentucky / Burundi | 2 |
| 6 | 16998 Mraz Lodge, Apt. 689 | North Omer / Kentucky / Gibraltar | 1 |
| 7 | 193 Marlin Brook, Apt. 406 | Herzoghaven / Arkansas / Cook Islands | 1 |
| 9 | 0643 Muller Vista, Suite 383 | Port Elvisfurt / NorthCarolina / Gabon | 3 |
| 10 | 7081 Eda Center, Suite 830 | Rutherfordtown / Delaware / Kyrgyz Republic | 1 |
| 12 | 979 Haag Shores Suite 656, Apt. 468 | Prohaskafort / SouthDakota / Lesotho | 2 |
| 14 | 186 Evan Motorway Apt. 409, Suite 079 | Lake Careyberg / Kentucky / New Caledonia | 2 |
| 15 | 74046 Metz Walk Apt. 113, Suite 674 | Port Oceane / Wyoming / Norfolk Island | 2 |

统计口径与说明：
- 「居住」采用 `students.current_address_id`（当前住址）与 `addresses.address_id` 关联，按地址去重学生数计数；9 个地址的学生数合计 15，与学生表 15 行一致。
- `addresses` 表共 15 条记录，其中 6 个地址当前没有学生居住（因此未列出）；学生表中 current_address_id 无空值。
- 另注：学生表还有 `permanent_address_id`（永久/家庭地址），若按该字段统计则为 8 个不同地址。由于「living there」更贴近当前住址，此处采用当前住址；如需改为永久地址口径可再出一版。
- 数据为样例库（各表 15 行），zip_postcode 为数值型编号（如 148、943），非标准邮编格式。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 591 · tvshow

**问题：** list all cartoon titles and their directors ordered by their air date

**参考 SQL：**

```sql
SELECT title ,  Directed_by FROM Cartoon ORDER BY Original_air_date
```

**完整参考结果：**

```json
[
  [
    "Invasion of the Secret Santas!",
    "Brandon Vietti"
  ],
  [
    "Evil Under the Sea!",
    "Michael Chang"
  ],
  [
    "Return of the Fearsome Fangs!",
    "Brandon Vietti"
  ],
  [
    "Deep Cover for Batman!",
    "Michael Chang"
  ],
  [
    "The Eyes of Despero!",
    "Ben Jones"
  ],
  [
    "Dawn of the Dead Man!",
    "Ben Jones"
  ],
  [
    "Day of the Dark Knight!",
    "Ben Jones"
  ],
  [
    "Fall of the Blue Beetle!",
    "Brandon Vietti"
  ],
  [
    "Journey to the Center of the Bat!",
    "Michael Chang"
  ],
  [
    "Enter the Outsiders!",
    "Michael Chang"
  ],
  [
    "The Rise of the Blue Beetle!",
    "Ben Jones"
  ],
  [
    "Terror on Dinosaur Island!",
    "Brandon Vietti"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT Title, Directed_by, CAST(STRPTIME(Original_air_date, '%B%d,%Y') AS DATE) AS air_date FROM cartoon ORDER BY air_date LIMIT 500
```

**Agent 最终回答：**

**卡通标题与导演（按首播日期升序）**

| # | 标题 | 导演 | 首播日期 |
|---|---|---|---|
| 1 | The Rise of the Blue Beetle! | Ben Jones | 2008-11-14 |
| 2 | Terror on Dinosaur Island! | Brandon Vietti | 2008-11-21 |
| 3 | Evil Under the Sea! | Michael Chang | 2008-12-05 |
| 4 | Invasion of the Secret Santas! | Brandon Vietti | 2008-12-12 |
| 5 | Day of the Dark Knight! | Ben Jones | 2009-01-02 |
| 6 | Enter the Outsiders! | Michael Chang | 2009-01-09 |
| 7 | Dawn of the Dead Man! | Ben Jones | 2009-01-16 |
| 8 | Fall of the Blue Beetle! | Brandon Vietti | 2009-01-23 |
| 9 | Journey to the Center of the Bat! | Michael Chang | 2009-01-30 |
| 10 | The Eyes of Despero! | Ben Jones | 2009-02-06 |
| 11 | Return of the Fearsome Fangs! | Brandon Vietti | 2009-02-20 |
| 12 | Deep Cover for Batman! | Michael Chang | 2009-02-27 |

**统计口径与说明**
- 数据源：`cartoon` 表全部 12 条记录（无重复行，Title 唯一，Directed_by 与 Original_air_date 均无空值）。
- 排序依据：`Original_air_date` 原为文本（如 "December12,2008"），若直接按文本排序会错乱，因此已按 `%B%d,%Y` 解析为日期后升序排列。
- 结果包含 12 部作品、3 位导演（Ben Jones、Brandon Vietti、Michael Chang），日期跨度 2008-11-14 至 2009-02-27，全部解析成功，无缺失日期。
- 局限：仅能说明该表内的单集首播日期，"Terror on Dinosaur Island!"（11-21）与 "Return of the Fearsome Fangs!"（02-20）之间有周次跳空，但表内无其他集信息，无法判断是否有未收录内容。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 597 · tvshow

**问题：** List the number of different series names and contents in the TV Channel table.

**参考 SQL：**

```sql
SELECT count(DISTINCT series_name) ,  count(DISTINCT content) FROM TV_Channel;
```

**完整参考结果：**

```json
[
  [
    15,
    1
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT COUNT(DISTINCT series_name) AS distinct_series_names, COUNT(DISTINCT Content) AS distinct_contents, COUNT(*) AS total_rows FROM tv_channel LIMIT 500
```

**Agent 最终回答：**

分析结果（表 `tv_channel`）：

| 指标 | 数量 |
|---|---|
| 不同 series_name（频道系列名） | 15 |
| 不同 Content（内容类型） | 1 |
| 总行数 | 15 |

**统计口径**：使用 `COUNT(DISTINCT ...)` 对 VARCHAR 字段去重计数，包含 NULL 之外的取值；未做空值排除处理。

**说明与不确定性**：
- 共 15 行，`series_name` 全部唯一（无重复），所以 15 个不同系列名 = 15 个频道。
- `Content` 只有 1 个不同取值，即全部 15 行均为 `"music"`。因此「不同内容数」为 1 属于数据本身的实际情况，而非查询错误；该表在内容类型上没有任何差异，若你期望看到多个内容类别，可能说明当前表只是一部分数据或筛选过的子集。

如需按国家/语言等维度进一步拆分，或查看每个系列名的其他属性，可继续告诉我。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 599 · tvshow

**问题：** What is the content of TV Channel with serial name "Sky Radio"?

**参考 SQL：**

```sql
SELECT Content FROM TV_Channel WHERE series_name = "Sky Radio";
```

**完整参考结果：**

```json
[
  [
    "music"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT series_name, Content, Country, Language, Hight_definition_TV, Pay_per_view_PPV, Package_Option FROM tv_channel WHERE LOWER(series_name) = 'sky radio' LIMIT 500
```

**Agent 最终回答：**

在 tv_channel 表中，series_name 为“Sky Radio”的频道有 1 条记录，其 **Content（内容类型）为 “music”（音乐）**。

完整信息：
- 频道名称：Sky Radio
- Content：music
- 国家/语言：Italy / Italian
- 高清（Hight_definition_TV）：no
- 按次付费（PPV）：no
- 套餐：Sky Famiglia

统计口径：按 `series_name` 精确匹配（不区分大小写）查询；表中共 15 行，该名称唯一命中 1 行，结果未截断。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 625 · tvshow

**问题：** List the Episode of all  TV series showed on TV Channel with series name "Sky Radio".

**参考 SQL：**

```sql
SELECT T2.Episode FROM TV_Channel AS T1 JOIN TV_series AS T2 ON T1.id = T2.Channel WHERE T1.series_name = "Sky Radio";
```

**完整参考结果：**

```json
[
  [
    "A Love of a Lifetime"
  ],
  [
    "Keepers"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT c.id AS channel_id, c.series_name, s.Episode, s.Air_Date, s.Viewers_m FROM tv_channel AS c LEFT JOIN tv_series AS s ON s.Channel = c.id WHERE LOWER(c.series_name) LIKE '%sky radio%' ORDER BY s.Air_Date, s.Episode LIMIT 500
```

**Agent 最终回答：**

**查询结果：频道 “Sky Radio”（频道 ID 700）播出的剧集共 2 条**

| Episode | Air_Date | Viewers_m |
|---|---|---|
| A Love of a Lifetime | September 24, 2007 | 9.16 |
| Keepers | October 29, 2007 | 5.75 |

**统计口径与说明**
- 关联逻辑：`tv_channel.series_name = 'Sky Radio'`（id=700）→ `tv_series.Channel = 700`，取 `Episode` 字段（原文为 VARCHAR，未做格式清洗）。
- 全库仅有一个 “Sky Radio” 频道（id=700，Content=music，Package_Option=Sky Famiglia），匹配采用不区分大小写的模糊匹配，未发现其他同名/近似频道。
- 该频道共 15 个频道中唯一命中，tv_series 中关联到 2 条记录，结果为完整集合（未截断）。
- 不确定点：`cartoon` 表中也有 2 条 Channel=700 的记录（“The Rise of the Blue Beetle!”、“Return of the Fearsome Fangs!”），但该表存的是动画标题而非 `Episode` 字段，故未计入；若你希望把卡通条目也算作“节目”，请告知，我可合并。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 632 · tvshow

**问题：** What are the package options and the name of the series for the TV Channel that supports high definition TV?

**参考 SQL：**

```sql
SELECT package_option ,  series_name FROM TV_Channel WHERE hight_definition_TV  =  "yes"
```

**完整参考结果：**

```json
[
  [
    "Sky Famiglia + Sky HD",
    "MTV Live HD"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT series_name, Package_Option FROM tv_channel WHERE LOWER(TRIM(Hight_definition_TV)) = 'yes' LIMIT 500
```

**Agent 最终回答：**

在 `tv_channel` 表中，只有 1 个频道标记为支持高清电视（`Hight_definition_TV = 'yes'`）：

| 系列名称 (series_name) | 套餐选项 (Package_Option) |
|---|---|
| MTV Live HD | Sky Famiglia + Sky HD |

统计口径：按 `tv_channel.Hight_definition_TV` 字段做不区分大小写、去除空格的匹配（值为 "yes"）；其余 14 个频道均为 "no"，因此结果唯一。注意该字段是文本型，本数据集只区分 yes/no，无法进一步核实具体高清规格；此外表中缺少对"TV Channel"本身的独立名称字段，故以 `series_name` 作为频道系列名。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 651 · poker_player

**问题：** List the final tables made and the best finishes of poker players.

**参考 SQL：**

```sql
SELECT Final_Table_Made ,  Best_Finish FROM poker_player
```

**完整参考结果：**

```json
[
  [
    42.0,
    1.0
  ],
  [
    10.0,
    2.0
  ],
  [
    21.0,
    1.0
  ],
  [
    19.0,
    2.0
  ],
  [
    26.0,
    3.0
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT p.Name, pp.Final_Table_Made, pp.Best_Finish FROM poker_player AS pp JOIN people AS p ON p.People_ID = pp.People_ID ORDER BY pp.Best_Finish LIMIT 500
```

**Agent 最终回答：**

**扑克选手进入决赛桌次数与最佳名次**（poker_player 表关联 people 表，按最佳名次升序；Best_Finish 为 1 表示冠军）

| 选手 | 进入决赛桌次数 | 最佳名次 |
|---|---|---|
| Yevgeni Sivozhelez | 21 | 1（冠军） |
| Aleksey Ostapenko | 42 | 1（冠军） |
| Teodor Salparov | 10 | 2（亚军） |
| Maksim Botin | 19 | 2（亚军） |
| Semen Poltavskiy | 26 | 3（季军） |

说明与不确定项：
- 口径：仅列出 poker_player 表中已有扑克记录的 5 人，内连接 people 表取姓名；两人并列最佳名次第 1、两人并列第 2。
- 表中「最佳名次」为数值字段，我按“数值越小越好（1 = 冠军）”理解，但在授权数据中未找到该字段的官方业务定义，若实际为相反方向（如 1 表示最差）则排序含义需反转。
- 5 名选手的姓名看起来与排球运动员同名（可能源自公开示例库），此处仅按数据原样呈现，不作身份推断。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 665 · poker_player

**问题：** What is the birth date of the poker player with the lowest earnings?

**参考 SQL：**

```sql
SELECT T1.Birth_Date FROM people AS T1 JOIN poker_player AS T2 ON T1.People_ID  =  T2.People_ID ORDER BY T2.Earnings ASC LIMIT 1
```

**完整参考结果：**

```json
[
  [
    "August 8, 1986"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT p.Name, p.Birth_Date, pp.Earnings, pp.Money_Rank FROM poker_player AS pp JOIN people AS p ON p.People_ID = pp.People_ID ORDER BY pp.Earnings ASC LIMIT 500
```

**Agent 最终回答：**

**Yevgeni Sivozhelez** 的出生日期是 **1986年8月8日**。

统计口径：以 `poker_player.Earnings` 的最小值确定“最低收入”（未对球员去重，因为 `poker_player` 中每个 People_ID 仅一条记录， Earnings 无空值）。该球员 Earnings = 104,871，Money_Rank = 166（5 名球员中排名最低者）。

不确定性：`Birth_Date` 为字符串格式（"August 8, 1986"），未做标准化处理；数据表中该球员姓名对应的项目字段（如国籍、身高）显示这可能是排球运动员而非扑克选手，因此“poker player”的归属可能有误，但就本表数据而言，收入最低者的出生日期即为上述值。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 669 · poker_player

**问题：** What is the average earnings of poker players with height higher than 200?

**参考 SQL：**

```sql
SELECT avg(T2.Earnings) FROM people AS T1 JOIN poker_player AS T2 ON T1.People_ID  =  T2.People_ID WHERE T1.Height  >  200
```

**完整参考结果：**

```json
[
  [
    309445.0
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT COUNT(*) AS players, AVG(p.Earnings) AS avg_earnings, MIN(p.Earnings) AS min_earnings, MAX(p.Earnings) AS max_earnings FROM poker_player AS p JOIN people AS pe ON p.People_ID = pe.People_ID WHERE pe.Height > 200 LIMIT 500
```

**Agent 最终回答：**

**结果：30 万美元（精确值 309,445）**

口径与依据：
- 人口表 `people`（身高，单位按数据推断为厘米）与 `poker_player`（Earnings 为奖金/收入）按 `People_ID` 内连接。
- 筛选条件 `Height > 200`，取 `AVG(Earnings)`；计算的是“每位球员一行”的算术平均，未做金额加权。

样本情况：
- 符合条件的仅 2 人：Aleksey Ostapenko（207 cm，476,090）和 Semen Poltavskiy（205 cm，142,800），因此平均值 = (476,090 + 142,800) / 2 = 309,445。
- 全表只有 5 名球员，样本极小，该平均值受个体影响极大，不宜外推。

不确定性：数据未提供 Earnings 的币种与时间范围，也未说明是否存在多赛季/多次记录；单位（身高 cm、金额）是从数值推断的。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 678 · poker_player

**问题：** Return the nationalities for which there are two or more people.

**参考 SQL：**

```sql
SELECT Nationality FROM people GROUP BY Nationality HAVING COUNT(*)  >=  2
```

**完整参考结果：**

```json
[
  [
    "Russia"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT Nationality, COUNT(*) AS People_Count FROM people WHERE NOT Nationality IS NULL GROUP BY Nationality HAVING COUNT(*) >= 2 ORDER BY Nationality LIMIT 500
```

**Agent 最终回答：**

按 `people.Nationality` 分组统计，至少有 2 人的国籍是：

- **Russia**：6 人

统计未包含国籍为空的记录。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 679 · poker_player

**问题：** List the names and birth dates of people in ascending alphabetical order of name.

**参考 SQL：**

```sql
SELECT Name ,  Birth_Date FROM people ORDER BY Name ASC
```

**完整参考结果：**

```json
[
  [
    "Aleksey Ostapenko",
    "May 26, 1986"
  ],
  [
    "Maksim Botin",
    "July 14, 1983"
  ],
  [
    "Roman Bragin",
    "April 17, 1987"
  ],
  [
    "Semen Poltavskiy",
    "February 8, 1981"
  ],
  [
    "Sergey Grankin",
    "January 22, 1987"
  ],
  [
    "Teodor Salparov",
    "August 16, 1982"
  ],
  [
    "Yevgeni Sivozhelez",
    "August 8, 1986"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT Name, Birth_Date FROM people ORDER BY Name ASC LIMIT 500
```

**Agent 最终回答：**

按姓名升序排列，结果如下（7 条记录均来自 people 表）：

| 姓名 | 出生日期 |
|---|---|
| Aleksey Ostapenko | May 26, 1986 |
| Maksim Botin | July 14, 1983 |
| Roman Bragin | April 17, 1987 |
| Semen Poltavskiy | February 8, 1981 |
| Sergey Grankin | January 22, 1987 |
| Teodor Salparov | August 16, 1982 |
| Yevgeni Sivozhelez | August 8, 1986 |

说明：
- 排序口径：按 Name 字段的字符串升序（A→Z），共 7 人，无重复姓名。
- Birth_Date 在表中以文本形式存储（如 "May 26, 1986"），此处按原值展示，未做日期格式转换。
- 结果未截断，完整返回。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 688 · voter_1

**问题：** List the contestant numbers and names, ordered by contestant name descending.

**参考 SQL：**

```sql
SELECT contestant_number ,  contestant_name FROM contestants ORDER BY contestant_name DESC
```

**完整参考结果：**

```json
[
  [
    2,
    "Tabatha Gehling"
  ],
  [
    8,
    "Nita Coster"
  ],
  [
    11,
    "Loraine NygrenTania Mattioli"
  ],
  [
    9,
    "Kurt Walser"
  ],
  [
    3,
    "Kelly Clauss"
  ],
  [
    6,
    "Jessie Eichman"
  ],
  [
    4,
    "Jessie Alloway"
  ],
  [
    10,
    "Ericka Dieter"
  ],
  [
    1,
    "Edwina Burnam"
  ],
  [
    7,
    "Allie Rogalski"
  ],
  [
    5,
    "Alana Bregman"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT contestant_number, contestant_name FROM contestants ORDER BY contestant_name DESC LIMIT 500
```

**Agent 最终回答：**

已从 `contestants` 表列出全部 11 名参赛者（表共 11 行，无截断），按姓名降序（字符比较）：

| 参赛者编号 | 姓名 |
|---|---|
| 2 | Tabatha Gehling |
| 8 | Nita Coster |
| 11 | Loraine NygrenTania Mattioli |
| 9 | Kurt Walser |
| 3 | Kelly Clauss |
| 6 | Jessie Eichman |
| 4 | Jessie Alloway |
| 10 | Ericka Dieter |
| 1 | Edwina Burnam |
| 7 | Allie Rogalski |
| 5 | Alana Bregman |

说明：
- 排序为按 `contestant_name` 的字符降序（区分大小写、按字母序），因此 "Jessie Eichman" 排在 "Jessie Alloway" 之前，符合 E > A 的字符顺序；"Nita" 排在 "Loraine" 之前，也是因为 N 的编码大于 L。
- 编号 11 的姓名在源数据中即为 "Loraine NygrenTania Mattioli"（疑似两段名字被拼接、缺少分隔符），此处按原始数据呈现，未做修改。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 710 · world_1

**问题：** Which region is the city Kabul located in?

**参考 SQL：**

```sql
SELECT Region FROM country AS T1 JOIN city AS T2 ON T1.Code  =  T2.CountryCode WHERE T2.Name  =  "Kabul"
```

**完整参考结果：**

```json
[
  [
    "Southern and Central Asia"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT c.Name AS City, c.District, c.CountryCode, co.Name AS Country, co.Continent, co.Region FROM city AS c JOIN country AS co ON c.CountryCode = co.Code WHERE c.Name = 'Kabul' LIMIT 500
```

**Agent 最终回答：**

Kabul（喀布尔）位于 **Southern and Central Asia（南亚与中亚）** 地区。

- 所属国家：Afghanistan（AFG，阿富汗）
- 洲：Asia
- 城市所在行政区（District）：Kabol

统计口径：city 表按城市名 `Name = 'Kabul'` 精确匹配（仅 1 条记录），再通过 CountryCode 关联 country 表取其 `Region` 字段。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 725 · world_1

**问题：** Give the average life expectancy for countries in Africa which are republics?

**参考 SQL：**

```sql
SELECT avg(LifeExpectancy) FROM country WHERE Continent  =  "Africa" AND GovernmentForm  =  "Republic"
```

**完整参考结果：**

```json
[
  [
    50.84347826086957
  ]
]
```

**Agent 主要 SQL：**

```sql
WITH r AS (SELECT GovernmentForm, LifeExpectancy FROM country WHERE Continent = 'Africa' AND GovernmentForm ILIKE '%republic%') SELECT COUNT(*) AS republic_count, COUNT(LifeExpectancy) AS countries_with_le, ROUND(AVG(LifeExpectancy), 2) AS avg_life_expectancy, ROUND(AVG(LifeExpectancy) FILTER(WHERE GovernmentForm = 'Republic'), 2) AS avg_exact_republic_only, ROUND(MIN(LifeExpectancy), 1) AS min_le, ROUND(MAX(LifeExpectancy), 1) AS max_le FROM r LIMIT 500
```

**Agent 最终回答：**

**结果：非洲共和制国家的平均预期寿命为 51.06 岁**

统计规范与说明：
- 数据源：`country` 表；筛选条件为 `Continent = 'Africa'`，且 `GovernmentForm` 包含 “Republic”（包括 Republic 46 个、Federal Republic 2 个、Islamic Republic 1 个，共 49 个国家）；按国家取算术平均，不按人口加权。
- 在这 49 个国家中，`LifeExpectancy` 字段都有值，无缺失，因此平均值的统计基础完整。范围从 37.2 岁（最低）到 73.7 岁（最高）。
- 如果把 “republic” 严格限定为 `GovernmentForm = 'Republic'`（46 国），平均为 **50.84 岁**；是否计入 Federal Republic、Islamic Republic 会使结果略有差异。
- 未使用人口加权，也无法排除各国预期寿命统计年份不同的影响。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 744 · world_1

**问题：** How many countries speak both English and Dutch?

**参考 SQL：**

```sql
SELECT COUNT(*) FROM (SELECT T1.Name FROM country AS T1 JOIN countrylanguage AS T2 ON T1.Code  =  T2.CountryCode WHERE T2.Language  =  "English" INTERSECT SELECT T1.Name FROM country AS T1 JOIN countrylanguage AS T2 ON T1.Code  =  T2.CountryCode WHERE T2.Language  =  "Dutch")
```

**完整参考结果：**

```json
[
  [
    3
  ]
]
```

**Agent 主要 SQL：**

```sql
WITH c AS (SELECT CountryCode, BOOL_OR(CAST(Language = 'English' AS BOOLEAN)) AS has_en, BOOL_OR(CAST(Language = 'Dutch' AS BOOLEAN)) AS has_nl, BOOL_OR(CAST(Language = 'English' AND IsOfficial = 'T' AS BOOLEAN)) AS has_en_off, BOOL_OR(CAST(Language = 'Dutch' AND IsOfficial = 'T' AS BOOLEAN)) AS has_nl_off FROM countrylanguage GROUP BY CountryCode) SELECT COUNT(*) FILTER(WHERE has_en AND has_nl) AS countries_both_any, COUNT(*) FILTER(WHERE has_en AND has_nl AND has_en_off AND has_nl_off) AS both_official, COUNT(*) FILTER(WHERE has_en) AS countries_with_english, COUNT(*) FILTER(WHERE has_nl) AS countries_with_dutch FROM c LIMIT 500
```

**Agent 最终回答：**

**答案：3 个国家**（口径：`countrylanguage` 表中同一条 `CountryCode` 下同时存在 Language = “English” 和 “Dutch” 的记录，官方/非官方均计入）。

明细：
| 国家 | 大洲 | 语言情况 |
|---|---|---|
| Aruba | 北美洲 | Dutch（官方, 5.3%）、English（非官方, 9.5%） |
| Canada | 北美洲 | English（官方, 60.4%）、Dutch（非官方, 0.5%） |
| Netherlands Antilles | 北美洲 | Dutch（官方, 0.0%）、English（非官方, 7.8%） |

其他参考数值：有英语记录的国家 60 个、有荷兰语记录的国家 5 个；同时以英语和荷兰语为**官方语言**的国家为 **0** 个。

说明与不确定性：
- 数据为世界数据库快照（239 个国家/地区、984 条语言记录），并非当前实时情况；表中 “Netherlands Antilles” 现已不存在，仍被保留在数据中。
- 判断仅基于语言名称精确匹配 “English”/“Dutch”，未包含其他语种或方言变体（如 “Frisian”）。
- 若你的口径要求两种语言都是官方语言，答案为 0；若还要求使用比例达到某阈值，结果也会不同——请告知口径可重新统计。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 805 · world_1

**问题：** Return the names of the 3 countries with the fewest people.

**参考 SQL：**

```sql
SELECT Name FROM country ORDER BY Population ASC LIMIT 3
```

**完整参考结果：**

```json
[
  [
    "Antarctica"
  ],
  [
    "French Southern territories"
  ],
  [
    "Bouvet Island"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT Name, Population FROM country ORDER BY Population ASC LIMIT 3
```

**Agent 最终回答：**

人口最少的 3 个国家或地区（按 country 表的 Population 字段排序）是：

| 排名 | 名称 | 人口 |
|---|---|---|
| 1 | Antarctica（南极洲） | 0 |
| 2 | French Southern territories（法属南部领地） | 0 |
| 3 | Bouvet Island（布韦岛） | 0 |

统计口径与不确定性说明：
- 数据来源是 country 表中的记录，每个国家一行，共 239 行；Population 字段没有空值。排序采用升序，人口相同时按任意顺序取前 3 条。
- 这 3 条记录存在并列：共有 7 条记录的 Population 为 0，因此“最少 3 个”的实际选择涉及并列取舍，换成这 7 条中的任意 3 条都同样成立；人口 >0 的最小记录是 50。
- 表中包含南极洲、法属南部领地、布韦岛等非主权属地，人口记为 0 表示无人常住，并非真实“国家人口”。如果你只想看主权国家，我可以排除人口为 0 的记录重新排序。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 814 · world_1

**问题：** Which language is spoken by the largest number of countries?

**参考 SQL：**

```sql
SELECT LANGUAGE FROM countrylanguage GROUP BY LANGUAGE ORDER BY count(*) DESC LIMIT 1
```

**完整参考结果：**

```json
[
  [
    "English"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT Language, COUNT(DISTINCT CountryCode) AS countries_total, COUNT(DISTINCT CASE WHEN IsOfficial = 'T' THEN CountryCode END) AS countries_official FROM countrylanguage GROUP BY Language ORDER BY countries_total DESC, countries_official DESC LIMIT 10
```

**Agent 最终回答：**

**English** — it is listed for the most countries: 60 distinct countries, of which 44 list it as an official language.

Top ranks (two counting ways):

| Language | Countries (any listing) | Countries (official) |
|---|---|---|
| English | 60 | 44 |
| Arabic | 33 | 22 |
| Spanish | 28 | 20 |
| French | 25 | 18 |
| German | 19 | 6 |
| Chinese | 19 | 2 |

Counting rules and caveats:
- Source: `countrylanguage` table (984 rows), counting **distinct `CountryCode`** per language, so one country counts once per language regardless of how many rows it has.
- Answer holds under both rules (all listings or `IsOfficial = 'T'` only); English leads either way.
- "Percentage" wasn't used as a threshold, so a language used by a tiny share of a country's population still counts as "spoken" there. If you prefer a minimum share (e.g. ≥1% or majority), the ranking could shift.
- It only covers the ~239 countries in this dataset; not all world territories are included.


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 840 · orchestra

**问题：** Show the names of conductors and the orchestras they have conducted.

**参考 SQL：**

```sql
SELECT T1.Name ,  T2.Orchestra FROM conductor AS T1 JOIN orchestra AS T2 ON T1.Conductor_ID  =  T2.Conductor_ID
```

**完整参考结果：**

```json
[
  [
    "Antal Doráti",
    "London Symphony Orchestra"
  ],
  [
    "Igor Stravinsky",
    "Columbia Symphony Orchestra"
  ],
  [
    "Colin Davis",
    "Royal Concertgebouw Orchestra"
  ],
  [
    "Paul Jorgensen",
    "Royal Danish Orchestra"
  ],
  [
    "Antal Brown",
    "Detroit Symphony Orchestra"
  ],
  [
    "Charles Dutoit",
    "Montreal Symphony Orchestra"
  ],
  [
    "Gerard Schwarz",
    "Seattle Symphony Orchestra"
  ],
  [
    "Pierre Boulez",
    "Chicago Symphony Orchestra"
  ],
  [
    "Valeri Gergiev",
    "Kirov Orchestra"
  ],
  [
    "Robert Craft",
    "Philharmonia Orchestra"
  ],
  [
    "Seiji Ozawa",
    "Orchestre de Paris"
  ],
  [
    "Michael Tilson Thomas",
    "San Francisco Symphony Orchestra"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT c.Name AS Conductor_Name, o.Orchestra FROM conductor AS c JOIN orchestra AS o ON o.Conductor_ID = c.Conductor_ID ORDER BY c.Name, o.Orchestra LIMIT 500
```

**Agent 最终回答：**

已按 **orchestra 表中的 Conductor_ID 关联 relation** 统计出指挥家及其对应的乐团，共 12 条记录，其中 10 位指挥家各对应 1 个乐团：

| 指挥家 | 乐团 |
|---|---|
| Antal Brown | Detroit Symphony Orchestra |
| Antal Doráti | London Symphony Orchestra |
| Charles Dutoit | Montreal Symphony Orchestra |
| Colin Davis | Royal Concertgebouw Orchestra |
| Gerard Schwarz | Seattle Symphony Orchestra |
| Igor Stravinsky | Columbia Symphony Orchestra |
| Michael Tilson Thomas | San Francisco Symphony Orchestra |
| Paul Jorgensen | Royal Danish Orchestra |
| Pierre Boulez | Chicago Symphony Orchestra |
| Robert Craft | Philharmonia Orchestra |
| Seiji Ozawa | Orchestre de Paris |
| Valeri Gergiev | Kirov Orchestra |

说明与不确定性：
- 统计口径为“orchestra 表中登记的常任/关联指挥”（每行一个乐团–指挥关系），而非 performance 明细中的实际演出记录；若“曾指挥过”需按演出场次追溯，performance 表本身不含指挥字段，无法支持该口径。
- 12 条关系对应 12 位不同指挥家、12 个不同乐团（即 12 行 = 12 位指挥家，只是我上面误写为 10 位，正确为 12 位指挥家、各对应 1 个乐团）。
- 该表未区分“在职期间”或时间范围，因此不代表完整的历任指挥历史。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 847 · orchestra

**问题：** What are the names of conductors who have conducted orchestras founded after the year 2008?

**参考 SQL：**

```sql
SELECT T1.Name FROM conductor AS T1 JOIN orchestra AS T2 ON T1.Conductor_ID  =  T2.Conductor_ID WHERE Year_of_Founded  >  2008
```

**完整参考结果：**

```json
[
  [
    "Igor Stravinsky"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT DISTINCT c.Name AS conductor_name, o.Orchestra, o.Year_of_Founded FROM conductor AS c JOIN orchestra AS o ON o.Conductor_ID = c.Conductor_ID WHERE o.Year_of_Founded > 2008 ORDER BY o.Year_of_Founded LIMIT 500
```

**Agent 最终回答：**

**结果：Igor Stravinsky**（哥伦比亚交响乐团，成立于 2009 年）。

统计口径与说明：
- 通过 `orchestra.Conductor_ID` → `conductor.Conductor_ID` 关联两张表，筛选 `Year_of_Founded > 2008`（严格晚于 2008 年，因此 2008 年成立的三个乐团——皇家音乐厅管弦乐团、基洛夫管弦乐团、旧金山交响乐团——不计入）。
- 按指挥家姓名去重，只有 1 位符合条件的指挥家。
- 数据规模很小（conductor 12 行、orchestra 12 行，全部可查、无截断）；`Year_of_Founded` 类型为浮点，但取值均为整年，无缺失值影响该筛选。
- 该答案仅反映此数据集中的「乐团—指挥」对应关系，不代表现实中的历史事实。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 1004 · singer

**问题：** What are the birth year and citizenship of singers?

**参考 SQL：**

```sql
SELECT Birth_Year ,  Citizenship FROM singer
```

**完整参考结果：**

```json
[
  [
    1944.0,
    "France"
  ],
  [
    1948.0,
    "United States"
  ],
  [
    1949.0,
    "United States"
  ],
  [
    1942.0,
    "Chile"
  ],
  [
    1940.0,
    "United States"
  ],
  [
    1953.0,
    "Australia"
  ],
  [
    1962.0,
    "Germany"
  ],
  [
    1961.0,
    "United States"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT Name, Birth_Year, Citizenship FROM singer ORDER BY Singer_ID LIMIT 500
```

**Agent 最终回答：**

**Singer 表中共有 8 位歌手，可查到全部 8 人的出生年份（Birth_Year）和国籍（Citizenship）：**

| 姓名 | 出生年份 | 国籍 |
|---|---|---|
| Liliane Bettencourt | 1944 | France |
| Christy Walton | 1948 | United States |
| Alice Walton | 1949 | United States |
| Iris Fontbona | 1942 | Chile |
| Jacqueline Mars | 1940 | United States |
| Gina Rinehart | 1953 | Australia |
| Susanne Klatten | 1962 | Germany |
| Abigail Johnson | 1961 | United States |

**统计口径与说明：**
- 数据来源：`singer` 表，按 `Singer_ID` 排序，逐行返回，全部 8 行，未截断。
- 出生年份跨度为 1940–1962 年；国籍共 5 个：United States 4 人，其余 France、Chile、Australia、Germany 各 1 人。
- 不确定性：`Birth_Year` 字段类型为 DOUBLE（浮点），此处按年份整数展示；表中没有出生年份或国籍为空的记录（8 行均有值），但我尚未单独做空值画像校验。此外，该表内容看起来像“财富榜人物”名单，字段含义是否与“歌手”业务定义一致，请以你的实际数据口径为准。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 1021 · singer

**问题：** what are the different names of the singers that have sales more than 300000?

**参考 SQL：**

```sql
SELECT DISTINCT T1.Name FROM singer AS T1 JOIN song AS T2 ON T1.Singer_ID  =  T2.Singer_ID WHERE T2.Sales  >  300000
```

**完整参考结果：**

```json
[
  [
    "Liliane Bettencourt"
  ],
  [
    "Christy Walton"
  ],
  [
    "Iris Fontbona"
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT s.Name, COUNT(g.Song_ID) AS Songs, SUM(g.Sales) AS Total_Sales, MAX(g.Sales) AS Max_Song_Sales FROM singer AS s JOIN song AS g ON s.Singer_ID = g.Singer_ID GROUP BY s.Singer_ID, s.Name HAVING SUM(g.Sales) > 300000 ORDER BY Total_Sales DESC LIMIT 500
```

**Agent 最终回答：**

歌手 Sales（销量）超过 300,000 的有 3 位：

| 歌手 | 歌曲数 | 总销量 | 单曲最高销量 |
|---|---|---|---|
| Liliane Bettencourt | 2 | 1,646,407 | 1,094,000 |
| Christy Walton | 2 | 651,421 | 351,421 |
| Iris Fontbona | 1 | 335,000 | 335,000 |

**统计口径与说明：**
- 通过 `song.Singer_ID = singer.Singer_ID` 关联，按歌手汇总 `Sales` 后筛选 `SUM(Sales) > 300000`（严格大于）。
- 两种理解方式结果一致：无论按“单曲销量 >300,000”还是“歌手总销量 >300,000”，都是同样这 3 位；被排除的 Jacqueline Mars（275,000）、Gina Rinehart（292,000）、Susanne Klatten（261,000）都不达标。
- Christy Walton 有一首歌销量正好等于 300,000（"Yeah"），未计入“超过”，但其另一首 351,421 已使其总销量达标。
- Alice Walton 和 Abigail Johnson 在 `song` 表中没有歌曲记录，因此不参与计算（并非销量为 0 的判定）。
- 数据量很小（`singer` 8 行、`song` 8 行），结果未被截断。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________

## 题目 1030 · real_estate_properties

**问题：** How many available features are there in total?

**参考 SQL：**

```sql
SELECT count(*) FROM Other_Available_Features
```

**完整参考结果：**

```json
[
  [
    3
  ]
]
```

**Agent 主要 SQL：**

```sql
SELECT COUNT(*) AS row_count, COUNT(DISTINCT feature_id) AS distinct_feature_id FROM other_available_features LIMIT 500
```

**Agent 最终回答：**

**总计 3 个可用特色（available features）。**

统计口径：`other_available_features` 表的记录数，按 `feature_id` 去重后仍为 3 条，无重复。

明细：
| feature_id | 类型 | 名称 | 说明 |
|---|---|---|---|
| 2 | Amenity | AirCon | 空调 |
| 3 | Amenity | Pool | 游泳池 |
| 4 | Security | BurglarAlarm | 防盗警报 |

需要注意的不确定性：`feature_id` 从 2 开始（缺少 1），说明可能存在一个未包含在当前授权数据中的特色记录（例如被删除、未启用或存在于其他表）；因此“3 个”是**当前数据集中可见**的可用特色数量，不一定等于系统中历史或全部的特色总数。此外，`other_property_features`（15 行）是房源与特色之间的关联/描述记录，不属于特色清单本身，故未计入。


**人工复核：** 审核人：________ 日期：________

- 正确性：□通过 □不通过 □无法判断
- 完整性：□通过 □不通过 □无法判断
- 证据支撑：□通过 □不通过 □无法判断
- 判断依据及与裁判分歧：____________________________
