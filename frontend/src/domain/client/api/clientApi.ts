import { http, unwrap } from "../../../shared/api/http";
import type { Client } from "../types";

export const clientApi = {
  /** 顧客の一覧（GET /clients/） */
  list(signal?: AbortSignal): Promise<Client[]> {
    return unwrap(http.GET("/clients/", { signal }));
  },
};
