import { test, expect } from "@playwright/test";
import { wholesale, command, read, nav } from "./business-helpers";

for (const category of ["DRUG", "DEVICE"]) {
  test(`${category} profile can be created, found by alias, revised and independently reviewed`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(90000);
    const owner = await wholesale(context, "demo.owner");
    const reviewer = await browser.newContext();
    const checker = await wholesale(reviewer, "demo.quality");
    const suffix = Date.now().toString(36);
    const year = new Date().getFullYear();
    const name = `档案字段测试-${category}-${suffix}`;
    const alias = `ALIAS-${suffix}`;
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
      await page.goto("/");
      await nav(page, "商品档案");
      await page.getByRole("button", { name: "新建商品", exact: true }).click();
      let dialog = page.getByRole("region", {
        name: "新建商品档案",
        exact: true,
      });
      for (const [label, value] of [
        ["商品编码", "P-" + suffix],
        ["名称", name],
        ["生产企业", "软件测试生产企业"],
        ["规格型号", "测试规格"],
        ["批准 / 注册 / 备案编号", "DEMO-ONLY"],
        ["基本单位", "盒"],
        ["资质生效日期", `${year}-01-01`],
        ["资质截止日期", `${year + 2}-12-31`],
      ])
        await dialog.getByLabel(label, { exact: true }).fill(value);
      await dialog.getByLabel("商品类别").selectOption(category);
      if (category === "DRUG") {
        await dialog.getByLabel("药品销售分类").selectOption("OTC");
        await dialog
          .getByLabel("药品通用名", { exact: true })
          .fill("测试药品通用名");
        await dialog.getByLabel("剂型", { exact: true }).fill("片剂");
        await dialog
          .getByLabel("上市许可持有人", { exact: true })
          .fill("虚拟持有人");
        await dialog
          .getByLabel("上市许可持有人地址", { exact: true })
          .fill("虚拟持有人地址");
        await expect(
          dialog.getByLabel("器械型号", { exact: true }),
        ).toHaveCount(0);
      } else {
        await dialog.getByLabel("器械管理类别").selectOption("II");
        await dialog.getByLabel("器械型号", { exact: true }).fill("TEST-MODEL");
        await dialog.getByLabel("注册人", { exact: true }).fill("虚拟注册人");
        await dialog
          .getByLabel("注册人地址", { exact: true })
          .fill("虚拟注册人地址");
        await expect(
          dialog.getByLabel("药品通用名", { exact: true }),
        ).toHaveCount(0);
      }
      await dialog.getByLabel("检索别名 / 助记码", { exact: true }).fill(alias);
      await dialog
        .getByLabel("商品名 / 品牌", { exact: true })
        .fill("测试品牌");
      await dialog
        .getByLabel("生产企业地址", { exact: true })
        .fill("虚拟生产地址");
      await dialog
        .getByLabel("储存条件（按标签 / 说明书）", { exact: true })
        .fill("按实际标签要求储存\n仅供软件测试");
      await dialog.screenshot({
        path: `../../artifacts/product-profile-${category}.png`,
      });
      await dialog
        .getByRole("button", { name: "保存待审核资料", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      await page
        .getByLabel("查询资料", { exact: true })
        .fill(alias.toLowerCase());
      await page.getByLabel("查询资料", { exact: true }).press("Enter");
      const row = page.getByRole("row").filter({ hasText: name });
      await expect(row).toHaveCount(1);
      const found = await read(
        context,
        `/api/v1/catalog/products?search=${alias}`,
      );
      expect(found.items).toHaveLength(1);
      const id = found.items[0].id;
      expect(found.items[0].search_alias).toBe(alias);
      expect(found.items[0].category).toBe(category);
      await command(
        reviewer,
        checker,
        `/api/v1/catalog/products/${id}/review`,
        {
          expected_version: "1",
          decision: "APPROVE",
          evidence_ref: "TEST-ONLY",
          reason: "核对虚拟商品档案",
        },
      );
      await row.getByRole("button", { name: "资料详情", exact: true }).click();
      dialog = page.getByRole("region", { name: "资料详情", exact: true });
      await dialog.getByRole("tab", { name: "质量管控", exact: true }).click();
      await expect(
        dialog.locator(".product-profile-details").filter({ visible: true }),
      ).toContainText(category === "DRUG" ? "虚拟持有人" : "虚拟注册人");
      await dialog
        .getByRole("button", { name: "修订资料", exact: true })
        .click();
      dialog = page.getByRole("region", { name: "修订资料", exact: true });
      await expect(
        dialog.getByLabel("商品名 / 品牌", { exact: true }),
      ).toHaveValue("测试品牌");
      await dialog
        .getByLabel("商品名 / 品牌", { exact: true })
        .fill("按标签修订品牌");
      await dialog
        .getByLabel("修订原因", { exact: true })
        .fill("按标签修订品牌");
      await dialog
        .getByRole("button", { name: "保存修订并重新送审", exact: true })
        .click();
      dialog = page.getByRole("region", { name: "资料详情", exact: true });
      await dialog.getByRole("tab", { name: "变更记录", exact: true }).click();
      await expect(dialog.getByTestId("catalog-history")).toContainText(
        "商品名 / 品牌",
      );
      await expect(dialog.getByTestId("catalog-history")).toContainText(
        "按标签修订品牌",
      );
      await dialog.getByRole("tab", { name: "质量管控", exact: true }).click();
      await expect(
        dialog.getByRole("button", { name: "提交审核", exact: true }),
      ).toBeDisabled();
      const revised = await read(context, `/api/v1/catalog/products/${id}`);
      expect(revised.record.approval_state).toBe("DRAFT");
      expect(revised.record.storage_conditions).toBe(
        "按实际标签要求储存\n仅供软件测试",
      );
      await command(
        reviewer,
        checker,
        `/api/v1/catalog/products/${id}/review`,
        {
          expected_version: revised.record.version,
          decision: "APPROVE",
          evidence_ref: "TEST-REVIEW",
          reason: "核对标签品牌变更",
        },
      );
      expect(errors).toEqual([]);
    } finally {
      await reviewer.close();
    }
  });
}
