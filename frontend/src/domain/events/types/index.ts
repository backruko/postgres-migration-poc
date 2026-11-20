// 出来事（アウトボックス。通知の仕組み）の型。OpenAPI から生成した型の別名で、手書きの型は持たない。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

export type DomainEvent = Schemas["DomainEventResponse"];
export type DomainEventList = Schemas["DomainEventListResponse"];

/** 一覧の絞り込み（from / to はオフセット付きの ISO 8601。from 以上・to 未満） */
export type EventFilter = {
  eventType?: string;
  clientCode?: string;
  orderId?: number;
  from?: string;
  to?: string;
  limit?: number;
};
