import { http, unwrap } from "../../../shared/api/http";
import type { BizDependency, BizDependencyCreate } from "../types";

/**
 * 業務間の依存関係の定義。作成・削除は業務管理者だけ（バックエンドが判定する）。
 * 定義を作ると、下流の業務の開始前のオーダーに上流のオーダーとのつながりができる。
 */
export const dependencyApi = {
  /** 顧客の業務どうしの依存関係すべて（GET /biz/clients/{code}/dependencies） */
  listForClient(clientCode: string, signal?: AbortSignal): Promise<BizDependency[]> {
    return unwrap(http.GET("/biz/clients/{client_code}/dependencies", { params: { path: { client_code: clientCode } }, signal }));
  },

  /** 下流の業務が上流の業務を待つ定義を作る（POST /biz/{id}/dependencies） */
  create(downstreamBizId: number, body: BizDependencyCreate): Promise<BizDependency> {
    return unwrap(http.POST("/biz/{biz_id}/dependencies", { params: { path: { biz_id: downstreamBizId } }, body }));
  },

  /** 定義を削除する（オーダーのつながりは「外れた」として残る）（DELETE /biz/dependencies/{id}） */
  remove(dependencyId: number): Promise<BizDependency> {
    return unwrap(http.DELETE("/biz/dependencies/{dependency_id}", { params: { path: { dependency_id: dependencyId } } }));
  },
};
