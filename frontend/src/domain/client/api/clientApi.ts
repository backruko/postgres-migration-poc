import { http, unwrap } from "../../../shared/api/http";
import type { Client, ClientCreate, ClientUpdate } from "../types";

export const clientApi = {
  /** 顧客の一覧（コード順）。無効にした顧客は includeInactive のときだけ（GET /clients/） */
  list(signal?: AbortSignal, includeInactive = false): Promise<Client[]> {
    return unwrap(http.GET("/clients/", { params: { query: { include_inactive: includeInactive } }, signal }));
  },

  /** 顧客の登録（業務管理者。コードは登録後に変えられない）（POST /clients/） */
  create(body: ClientCreate): Promise<Client> {
    return unwrap(http.POST("/clients/", { body }));
  },

  /** 顧客名の変更（業務管理者）（PATCH /clients/{id}） */
  update(clientId: number, body: ClientUpdate): Promise<Client> {
    return unwrap(http.PATCH("/clients/{client_id}", { params: { path: { client_id: clientId } }, body }));
  },

  /** 顧客を無効にする（業務管理者。有効な業務が残っていると 400）（POST /clients/{id}/deactivate） */
  deactivate(clientId: number): Promise<Client> {
    return unwrap(http.POST("/clients/{client_id}/deactivate", { params: { path: { client_id: clientId } } }));
  },

  /** 無効にした顧客を再び有効にする（業務管理者）（POST /clients/{id}/activate） */
  activate(clientId: number): Promise<Client> {
    return unwrap(http.POST("/clients/{client_id}/activate", { params: { path: { client_id: clientId } } }));
  },
};
