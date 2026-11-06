import { DownOutlined, UserOutlined } from "@ant-design/icons";
import { Dropdown, Modal } from "antd";
import type { MenuProps } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { authApi } from "../domain/staff/api";
import type { Staff } from "../domain/staff/types";
import { PasswordChangeForm } from "../features/auth/PasswordChangeForm";
import { markSignedOut } from "../shared/api/session";

/** ナビのログイン中の職員（LG.3: 「操作する職員」の置き換え）。パスワードの変更とログアウト */
export function UserMenu({ me, onChanged }: { me: Staff; onChanged: () => void }) {
  const navigate = useNavigate();
  const [passwordOpen, setPasswordOpen] = useState(false);

  const logout = async () => {
    await authApi.logout();
    markSignedOut();
    navigate("/login", { replace: true });
  };

  const menu: MenuProps = {
    items: [
      { key: "who", label: <span className="muted">ログインID: <span className="mono">{me.login_id}</span></span>, disabled: true },
      { type: "divider" },
      { key: "password", label: "パスワードを変更" },
      { key: "logout", label: "ログアウト", danger: true },
    ],
    onClick: ({ key }) => {
      if (key === "password") setPasswordOpen(true);
      if (key === "logout") void logout();
    },
  };

  return (
    <>
      <Dropdown menu={menu} trigger={["click"]} placement="bottomRight">
        <button type="button" className="acting-staff" aria-haspopup="menu">
          <UserOutlined />
          <span className="acting-staff-name">{me.name}</span>
          <DownOutlined className="nav-menu-caret" />
        </button>
      </Dropdown>
      <Modal open={passwordOpen} title="パスワードを変更" footer={null} onCancel={() => setPasswordOpen(false)} destroyOnClose>
        <PasswordChangeForm
          me={me}
          onChanged={() => {
            setPasswordOpen(false);
            onChanged();
          }}
          onCancel={() => setPasswordOpen(false)}
        />
      </Modal>
    </>
  );
}
