import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { orderApi } from "../api";

/** オーダーの詳細と記録。orderId が null の間は取得しない */
export function useOrder(orderId: number | null) {
  return useApiQuery((signal) => orderApi.get(orderId as number, signal), [orderId], {
    enabled: orderId !== null,
    keepDataOnError: () => false,
  });
}
