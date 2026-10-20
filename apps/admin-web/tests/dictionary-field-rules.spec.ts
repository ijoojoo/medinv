import { test, expect } from "@playwright/test";
import { wholesale, read, command, nav } from "./business-helpers";
import {
  writableRules,
  type ProductFieldPolicy,
} from "../src/productFieldRules";
import type { CatalogDictionary } from "../src/catalogDictionaries";

test("field rules and dictionary maintenance retain drafts, filter choices and prevent required-field conflicts", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const dictPath = "/api/v1/catalog/dictionaries/trade_name";
  const rulePath = "/api/v1/catalog/field-rules/";
  const originalDictionary = (await read(
    context,
    dictPath,
  )) as CatalogDictionary;
  const policies = await Promise.all(
    ["DEVICE", "DRUG"].map(
      async (category) =>
        (await read(context, rulePath + category)) as ProductFieldPolicy,
    ),
  );
  const invalid = "LINK-" + Date.now().toString(36);
  const valid = String(Date.now()).slice(-7);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  async function setRules(
    policy: ProductFieldPolicy,
    fields = writableRules(policy.fields),
  ) {
    const current = (await read(
      context,
      rulePath + policy.category,
    )) as ProductFieldPolicy;
    if (
      JSON.stringify(writableRules(current.fields)) !== JSON.stringify(fields)
    ) {
      await command(context, owner, rulePath + policy.category, {
        expected_version: current.version,
        fields,
        reason: "字段字典衔接验收 / 恢复",
      });
    }
  }
  try {
    for (const policy of policies) {
      await setRules(
        policy,
        writableRules(policy.fields).map((f) =>
          f.key === "trade_name"
            ? {
                ...f,
                visible: true,
                required: false,
                required_when: [],
                min_length: 0,
                max_length: 100,
                validator: "NONE",
              }
            : f,
        ),
      );
    }
    await command(context, owner, dictPath, {
      expected_version: originalDictionary.version,
      mode: "STRICT",
      items: [
        ...originalDictionary.items.map((i) => ({ ...i, active: false })),
        { value: invalid, active: true, order: 9998 },
      ],
      reason: "准备字段字典联动验收",
    });
    // The fixed-slot MDM configuration replaced the former field-settings screen.
    // Exercise the current dictionary workspace; retain the API binding checks.
    const numericRules = writableRules(policies[0].fields).map((f) =>
      f.key === "trade_name"
        ? {
            ...f,
            required: false,
            required_when: [],
            min_length: 7,
            max_length: 7,
            validator: "DIGITS" as const,
          }
        : f,
    );
    await setRules(policies[0], numericRules);
    await page.goto("/");
    await nav(page, "商品字典");
    const dictionary = page.getByRole("region", {
      name: "商品字典",
      exact: true,
    });
    await dictionary
      .getByRole("navigation", { name: "字典字段" })
      .getByRole("button", { name: /商品名 \/ 品牌/ })
      .click();
    await expect(
      dictionary.getByRole("region", { name: "字典适用规则" }),
    ).toContainText("不符合规则：" + invalid);
    await dictionary
      .getByRole("button", { name: "新增字典值", exact: true })
      .click();
    const index = originalDictionary.items.length + 2;
    await dictionary.getByLabel(`字典值 ${index}`, { exact: true }).fill(valid);
    await dictionary
      .getByLabel("变更说明", { exact: true })
      .fill("补充符合数字格式的字典值");
    await page
      .getByRole("button", { name: "关闭商品字典", exact: true })
      .click();
    const leave = page.getByRole("dialog", {
      name: "关闭前保存修改",
      exact: true,
    });
    await leave.getByRole("button", { name: "取消关闭", exact: true }).click();
    await nav(page, "商品档案");
    await nav(page, "商品字典");
    await expect(
      dictionary.getByLabel(`字典值 ${index}`, { exact: true }),
    ).toHaveValue(valid);
    await dictionary
      .getByRole("button", { name: "保存字典", exact: true })
      .click();
    await expect(
      dictionary.getByLabel("变更说明", { exact: true }),
    ).toHaveValue("");
    await expect(
      dictionary.getByLabel(`字典值 ${index}`, { exact: true }),
    ).toHaveAttribute("readonly", "");
    await setRules(
      policies[0],
      numericRules.map((f) =>
        f.key === "trade_name"
          ? { ...f, required_when: [{ field: "device_class", equals: "III" }] }
          : f,
      ),
    );
    await page.reload();
    await nav(page, "商品字典");
    await dictionary
      .getByRole("navigation", { name: "字典字段" })
      .getByRole("button", { name: /商品名 \/ 品牌/ })
      .click();
    await expect(
      dictionary.getByRole("region", { name: "字典适用规则" }),
    ).toContainText("含条件必填");
    const saved = (await read(context, dictPath)) as CatalogDictionary;
    const validIndex = saved.items.findIndex((i) => i.value === valid) + 1;
    await dictionary
      .getByLabel(`启用 ${validIndex}`, { exact: true })
      .uncheck();
    await dictionary
      .getByLabel("变更说明", { exact: true })
      .fill("尝试停用最后一个有效值");
    await expect(dictionary.getByRole("alert")).toContainText(
      "必填字段没有符合规则的可选值",
    );
    await expect(
      dictionary.getByRole("button", { name: "保存字典", exact: true }),
    ).toBeDisabled();
    let writes = 0;
    page.on("request", (r) => {
      if (r.method() === "POST" && new URL(r.url()).pathname === dictPath)
        writes++;
    });
    await dictionary
      .locator("form")
      .evaluate((form: HTMLFormElement) => form.requestSubmit());
    expect(writes).toBe(0);
    expect(
      ((await read(context, dictPath)) as CatalogDictionary).items.find(
        (i) => i.value === valid,
      )?.active,
    ).toBe(true);
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await dictionary.screenshot({
      path: "../../artifacts/dictionary-field-conflict.png",
    });
    await dictionary
      .getByRole("button", { name: "撤销字典修改", exact: true })
      .click();
    await expect(dictionary.getByRole("alert")).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    // Relax only this test's required field before restoring the original choices.
    for (const policy of policies) {
      await setRules(
        policy,
        writableRules(policy.fields).map((f) =>
          f.key === "trade_name"
            ? {
                ...f,
                required: false,
                required_when: [],
              }
            : f,
        ),
      );
    }
    const current = (await read(context, dictPath)) as CatalogDictionary;
    await command(context, owner, dictPath, {
      expected_version: current.version,
      mode: originalDictionary.mode,
      items: current.items.map(
        (i) =>
          originalDictionary.items.find((o) => o.value === i.value) || {
            ...i,
            active: false,
          },
      ),
      reason: "恢复验收前字典，新增测试值停用保留",
    });
    for (const policy of policies) await setRules(policy);
  }
});
