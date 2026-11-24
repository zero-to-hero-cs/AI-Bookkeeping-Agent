/*
 * calc.c —— C语言本地账目计算、分类统计与超支判定
 *
 * 输入（都在 data/ 下，相对项目根目录）：
 *   data/query.txt   3 行：类别（或 ALL） / 开始日期 / 结束日期
 *   data/data.txt    每行：日期,类别,金额     （兼容老格式：类别,金额）
 *   data/budget.txt  一个数字（可以不存在）
 *
 * 输出：
 *   data/result.txt  一行：总计:702.11, 交通:50.00, 餐饮:148.10, [WARNING]
 *
 * 注意：本文件必须保存为 UTF-8 编码。写进 result.txt 的"总计"用 UTF-8 转义写法，
 *       这样即使编译器的源码编码不是 UTF-8，输出的字节也不会乱码。
 *
 * 编译方法（任选其一）：
 *   MinGW / gcc   ：gcc calc.c -o calc.exe -Wall -O2
 *   Visual Studio ：cl /utf-8 /O2 calc.c        （在"开发者命令提示符"里执行）
 *                   文件名必须是 calc.c，否则生成的 exe 名字不对，界面找不到
 */
#define _CRT_SECURE_NO_WARNINGS     /* MSVC 下屏蔽 strncpy 等函数的"不安全"警告，gcc 无影响 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_CATS  32        /* 最多支持多少种分类 */
#define NAME_LEN  64        /* 分类名长度（一个汉字 3 字节） */
#define DATE_LEN  16        /* 日期 YYYY-MM-DD 共 10 字节 */
#define LINE_LEN  512       /* 一行最长字节数 */

/* "总计"两个字的 UTF-8 字节，写成转义形式，避免受源码编码影响 */
#define LABEL_TOTAL "\xE6\x80\xBB\xE8\xAE\xA1"

/* 去掉行尾的 \r \n 和空格 */
static void trim_eol(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' ||
                     s[n - 1] == ' ' || s[n - 1] == '\t')) {
        s[n - 1] = '\0';
        n--;
    }
}

/* 把 src 复制到 dst，限制长度，不会溢出 */
static void copy_field(char *dst, size_t size, const char *src)
{
    if (size == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    strncpy(dst, src, size - 1);
    dst[size - 1] = '\0';
}

/* 在分类表里找 name：找到返回下标，没找到就新增，表满返回 -1 */
static int find_or_add(char names[][NAME_LEN], int *count, const char *name)
{
    int i;
    for (i = 0; i < *count; i++) {
        if (strcmp(names[i], name) == 0) {
            return i;
        }
    }
    if (*count >= MAX_CATS) {
        return -1;
    }
    copy_field(names[*count], NAME_LEN, name);
    (*count)++;
    return *count - 1;
}

int main(void)
{
    /* ---------- 变量统一在开头声明 ---------- */
    char keyword[NAME_LEN] = "ALL";
    char start[DATE_LEN] = "0000-01-01";
    char end[DATE_LEN] = "9999-12-31";

    char names[MAX_CATS][NAME_LEN];
    double sums[MAX_CATS];
    int cat_count = 0;

    double filtered_total = 0.0;
    double budget = 0.0;
    int has_budget = 0;

    char line[LINE_LEN];
    FILE *fp;
    int i;

    for (i = 0; i < MAX_CATS; i++) {
        names[i][0] = '\0';
        sums[i] = 0.0;
    }

    /* ================================================================
     * 1. 读查询条件 data/query.txt
     *    第 1 行类别（ALL 表示不分类），第 2 行开始日期，第 3 行结束日期
     *    文件不存在或行不全时，退化成"全部 + 不限日期"，不报错
     * ================================================================ */
    fp = fopen("data/query.txt", "r");
    if (fp != NULL) {
        if (fgets(line, sizeof(line), fp) != NULL) {
            trim_eol(line);
            if (line[0] != '\0') copy_field(keyword, sizeof(keyword), line);
        }
        if (fgets(line, sizeof(line), fp) != NULL) {
            trim_eol(line);
            if (line[0] != '\0') copy_field(start, sizeof(start), line);
        }
        if (fgets(line, sizeof(line), fp) != NULL) {
            trim_eol(line);
            if (line[0] != '\0') copy_field(end, sizeof(end), line);
        }
        fclose(fp);
    }

    /* ================================================================
     * 2. 读账本 data/data.txt，按日期区间过滤后累加
     *    每行：日期,类别,金额      （老格式只有 类别,金额，也算有效数据）
     * ================================================================ */
    fp = fopen("data/data.txt", "r");
    if (fp == NULL) {
        printf("[calc] data/data.txt not found, treat as empty\n");
    } else {
        while (fgets(line, sizeof(line), fp) != NULL) {
            char date[DATE_LEN];
            char category[NAME_LEN];
            const char *amount_text;
            char *first_comma;
            char *second_comma;
            double amount;
            int index;

            trim_eol(line);
            if (line[0] == '\0' || line[0] == '#') {
                continue;                       /* 空行和注释行 */
            }

            first_comma = strchr(line, ',');
            if (first_comma == NULL) {
                continue;                       /* 没有逗号，坏行 */
            }
            *first_comma = '\0';

            second_comma = strchr(first_comma + 1, ',');
            if (second_comma == NULL) {
                /* 只有两段：老格式 类别,金额 */
                date[0] = '\0';
                copy_field(category, sizeof(category), line);
                amount_text = first_comma + 1;
            } else {
                /* 三段：日期,类别,金额 */
                *second_comma = '\0';
                copy_field(date, sizeof(date), line);
                copy_field(category, sizeof(category), first_comma + 1);
                amount_text = second_comma + 1;
            }

            amount = atof(amount_text);
            if (amount <= 0.0) {
                continue;                       /* 金额不合法，跳过 */
            }

            /* 日期是定长 YYYY-MM-DD，直接用 strcmp 比大小即可 */
            if (date[0] != '\0' &&
                (strcmp(date, start) < 0 || strcmp(date, end) > 0)) {
                continue;
            }

            index = find_or_add(names, &cat_count, category);
            if (index >= 0) {
                sums[index] += amount;
                /* 总计只算命中的分类；ALL 表示全都要 */
                if (strcmp(keyword, "ALL") == 0 || strcmp(category, keyword) == 0) {
                    filtered_total += amount;
                }
            }
        }
        fclose(fp);
    }

    /* ================================================================
     * 3. 读预算 data/budget.txt，做超支判定
     * ================================================================ */
    fp = fopen("data/budget.txt", "r");
    if (fp != NULL) {
        if (fscanf(fp, "%lf", &budget) == 1 && budget > 0.0) {
            has_budget = 1;
        }
        fclose(fp);
    }

    /* ================================================================
     * 4. 写结果 data/result.txt
     * ================================================================ */
    fp = fopen("data/result.txt", "w");
    if (fp == NULL) {
        printf("[calc] ERROR: cannot write data/result.txt\n");
        return 1;
    }

    fprintf(fp, LABEL_TOTAL ":%.2f", filtered_total);
    for (i = 0; i < cat_count; i++) {
        fprintf(fp, ", %s:%.2f", names[i], sums[i]);
    }
    if (has_budget && filtered_total > budget) {
        fprintf(fp, ", [WARNING]");
    }
    fprintf(fp, "\n");
    fclose(fp);

    printf("[calc] total=%.2f categories=%d warning=%d\n",
           filtered_total, cat_count, (has_budget && filtered_total > budget) ? 1 : 0);
    return 0;
}
