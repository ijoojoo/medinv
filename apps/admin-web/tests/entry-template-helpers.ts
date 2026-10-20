// Exercise both channels through their independent entity-scoped column contracts.
import {
  read as readBusiness,
  command as commandBusiness,
} from "./business-helpers";
import type { BrowserContext } from "@playwright/test";
export const entryTemplatePath = (purchase: boolean, entity: string) =>
  purchase
    ? `/api/v1/document-columns/orders/editor?entity_id=${entity}`
    : `/api/v1/document-columns/sales_orders/editor?entity_id=${entity}`;
export async function read(context: BrowserContext, path: string) {
  const value = await readBusiness(context, path);
  return path.startsWith("/api/v1/document-columns/")
    ? { ...value, template: { schema_version: 1, columns: value.columns } }
    : value;
}
export async function command(
  context: BrowserContext,
  owner: any,
  path: string,
  body: any,
) {
  if (path.startsWith("/api/v1/document-columns/") && body.template)
    body = {
      expected_version: body.expected_version,
      schema_version: 1,
      columns: body.template.columns.map(
        ({ required, editable, ...column }: any) => column,
      ),
    };
  return commandBusiness(context, owner, path, body);
}
