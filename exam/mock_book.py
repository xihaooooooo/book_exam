"""模拟一本 Python 入门教材的目录和章节正文，替代 PDF 解析器。"""

TOC = [
    {
        "chapter": "第1章 变量和数据类型",
        "sections": [
            {"id": "1.1", "title": "1.1 什么是变量"},
            {"id": "1.2", "title": "1.2 字符串"},
            {"id": "1.3", "title": "1.3 数字"},
            {"id": "1.4", "title": "1.4 类型转换"},
        ]
    },
    {
        "chapter": "第2章 列表",
        "sections": [
            {"id": "2.1", "title": "2.1 列表是什么"},
            {"id": "2.2", "title": "2.2 修改、添加和删除元素"},
            {"id": "2.3", "title": "2.3 组织列表"},
        ]
    },
    {
        "chapter": "第3章 条件判断",
        "sections": [
            {"id": "3.1", "title": "3.1 if 语句"},
            {"id": "3.2", "title": "3.2 if-else 语句"},
            {"id": "3.3", "title": "3.3 多条件判断"},
        ]
    },
]

SECTIONS = {
    "1.1": """1.1 什么是变量

变量是存储数据的容器。在 Python 中，你不需要声明变量的类型，Python 会根据赋给变量的值自动推断类型。

创建变量的方式很简单，就是用一个名字 = 一个值：

    name = "Alice"
    age = 25

变量名的命名规则：
- 只能包含字母、数字和下划线
- 不能以数字开头
- 不能使用 Python 的关键字（如 if、for、while）
- 区分大小写（Name 和 name 是不同的变量）

好的变量名应该具有描述性，让人一眼就能看出它存储的是什么数据。""",

    "1.2": """1.2 字符串

字符串是一系列字符的序列，在 Python 中用单引号或双引号括起来：

    "Hello"
    'Python'

字符串的常用方法：
- title()：将每个单词的首字母大写
- upper()：将所有字母转为大写
- lower()：将所有字母转为小写
- strip()：删除字符串首尾的空白字符
- replace(old, new)：替换字符串中的内容

字符串可以用 + 拼接，用 * 重复：
    "Hello" + " " + "World"  → "Hello World"
    "Hi" * 3                 → "HiHiHi"

f-string 是格式化字符串的便捷方式：
    name = "Alice"
    f"Hello, {name}!"  → "Hello, Alice!"
""",

    "1.3": """1.3 数字

Python 支持整数和浮点数：

    整数（int）：10, -5, 0
    浮点数（float）：3.14, -0.5, 1.0

基本运算：
    加 +  减 -  乘 *  除 /  整除 //  取余 %  幂 **

    10 / 3   → 3.333...
    10 // 3  → 3   （整除，向下取整）
    10 % 3   → 1   （取余数）
    2 ** 3   → 8   （2的3次方）

注意：整数和浮点数运算时，结果会是浮点数：
    1 + 2.0  → 3.0

数字中可以用下划线分隔，提高可读性：
    1_000_000  → 1000000""",

    "1.4": """1.4 类型转换

Python 提供了类型转换函数，在不同数据类型之间转换：

    str()  → 转为字符串
    int()  → 转为整数
    float() → 转为浮点数

    str(123)     → "123"
    int("456")   → 456
    float("3.14") → 3.14

注意：int() 转换字符串时，字符串必须是纯数字格式，否则会报错：
    int("abc")   → ValueError
    int("3.14")  → ValueError（带小数点的字符串不能直接转 int）

类型转换在接收用户输入时特别重要，因为 input() 函数总是返回字符串：
    age = input("请输入年龄: ")   # "25"
    age = int(age)               # 25""",

    "2.1": """2.1 列表是什么

列表（list）是 Python 中最常用的数据结构之一，是一个有序的可变序列。

列表用方括号 [] 定义，元素之间用逗号分隔：
    fruits = ["apple", "banana", "orange"]
    numbers = [1, 2, 3, 4, 5]
    mixed = [1, "hello", 3.14, True]   # 列表可以包含不同类型

访问列表元素通过索引（从 0 开始）：
    fruits[0]   → "apple"   （第一个元素）
    fruits[-1]  → "orange"  （最后一个元素）

列表是有序的：元素的位置是固定的，插入顺序就是它们在列表中的排列顺序。
列表是可变的：可以修改、添加、删除元素。""",

    "2.2": """2.2 修改、添加和删除元素

修改元素：直接用索引赋值
    fruits[0] = "pear"

添加元素的方法：
- append(x)：在列表末尾添加元素 x
    fruits.append("grape")   → ["apple", "banana", "orange", "grape"]
- insert(i, x)：在索引 i 处插入元素 x
    fruits.insert(1, "kiwi")  → ["apple", "kiwi", "banana", "orange"]

删除元素的方法：
- del 语句：按索引删除
    del fruits[0]
- pop(i)：删除索引 i 的元素并返回该元素（默认删除最后一个）
    last = fruits.pop()       # 删除并返回最后一个
    second = fruits.pop(1)    # 删除并返回索引1的元素
- remove(x)：按值删除（只删除第一个匹配项）
    fruits.remove("banana")

append 和 insert 的区别：
- append(x) 只有一个参数，总是在末尾添加，O(1)
- insert(i, x) 有两个参数，可以在任意位置插入，O(n)
- 容易混淆的是参数顺序：insert 的第一个参数是索引，第二个才是值""",

    "2.3": """2.3 组织列表

排序方法：
- sort()：就地排序（修改原列表），默认升序
    nums = [3, 1, 4, 1, 5]
    nums.sort()          → [1, 1, 3, 4, 5]
    nums.sort(reverse=True) → [5, 4, 3, 1, 1]

- sorted()：返回一个新列表，原列表不变
    nums = [3, 1, 4, 1, 5]
    new_nums = sorted(nums)  → [1, 1, 3, 4, 5]
    # nums 仍然是 [3, 1, 4, 1, 5]

sort() 和 sorted() 的关键区别：
- sort() 是列表的方法，修改原列表，返回 None
- sorted() 是内置函数，返回新列表，原列表不变
- sorted() 适用于任何可迭代对象（列表、元组、字符串等）
- sort() 只能用于列表

反转列表：
- reverse()：就地反转
    nums.reverse()  → [5, 1, 4, 1, 3]

获取列表长度：len(fruits) → 列表元素个数""",

    "3.1": """3.1 if 语句

if 语句用于条件判断，根据条件是否成立决定是否执行某段代码。

    age = 18
    if age >= 18:
        print("你是成年人")

if 语句的结构：
- if 关键字开头
- 后面是条件表达式（可以理解为 True 或 False 的问题）
- 条件后跟冒号 :
- 缩进的代码块是条件成立时执行的

条件表达式常用比较运算符：
    == 等于    != 不等于
    >  大于    <  小于
    >= 大于等于  <= 小于等于

注意：= 是赋值，== 是比较，两者不能混用！
    if name = "Alice":    # 错误！这是赋值
    if name == "Alice":   # 正确""",

    "3.2": """3.2 if-else 语句

if-else 语句处理条件不成立的情况：

    age = 16
    if age >= 18:
        print("你可以投票")
    else:
        print("你还不能投票")

如果条件成立 → 执行 if 代码块
如果条件不成立 → 执行 else 代码块

if-else 是互斥的：一个条件判断中，if 和 else 只有一个会被执行。""",

    "3.3": """3.3 多条件判断

使用 if-elif-else 处理多个条件：

    score = 85
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    else:
        grade = "D"

执行逻辑：
- 从上到下依次检查每个条件
- 第一个成立的条件对应的代码块被执行
- 后面的条件不会再检查
- 如果所有条件都不成立，执行 else（可选）

可以同时使用多个逻辑运算符：
    and：两个条件同时成立
    or：至少一个条件成立
    not：取反

    if age >= 18 and has_id:
        print("可以进入")

    if score < 0 or score > 100:
        print("无效分数")

if-elif-else 链和多个独立 if 的区别：
- if-elif-else 是互斥的，只执行第一个成立的
- 多个独立 if 是各自独立的，每个都会检查""",
}
