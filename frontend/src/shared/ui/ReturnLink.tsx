import { ArrowLeftOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useReturnTo } from "../lib/returnTo";

/** ショートカットで開いた画面にだけ出る「← 戻る」リンク（ページ見出しの下に置く） */
export function ReturnLink() {
  const r = useReturnTo();
  if (!r) return null;
  return (
    <Link to={r.to} state={r.state} className="return-link">
      <ArrowLeftOutlined /> {r.label}
    </Link>
  );
}
