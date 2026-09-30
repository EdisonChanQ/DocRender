import { NavLink, Route, Routes } from "react-router-dom";

import CategoryPage from "./pages/CategoryPage";
import DatabasePage from "./pages/DatabasePage";
import FileJobPage from "./pages/FileJobPage";
import FlowTestPage from "./pages/FlowTestPage";
import LlmToTextPage from "./pages/LlmToTextPage";
import OcrToTextPage from "./pages/OcrToTextPage";
import ParsePage from "./pages/ParsePage";
import PathConfigPage from "./pages/PathConfigPage";
import QrToTextPage from "./pages/QrToTextPage";
import TemplateFieldsPage from "./pages/TemplateFieldsPage";
import TemplateMakerPage from "./pages/TemplateMakerPage";
import TemplatePage from "./pages/TemplatePage";

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

interface NavGroup {
  label?: string;
  items: NavItem[];
}

// 分组导航：顶层直链 + "工具管理"子菜单（OCR-to-Text 后续在此追加）
const NAV_GROUPS: NavGroup[] = [
  {
    items: [
      { to: "/", label: "文件解析", end: true },
      { to: "/categories", label: "分类管理" },
      { to: "/templates", label: "模板管理" },
      { to: "/path-configs", label: "路径管理" },
      { to: "/file-jobs", label: "文件任务" },
      { to: "/flow-test", label: "流程测试" },
    ],
  },
  {
    label: "工具管理",
    items: [
      { to: "/tools/qr-to-text", label: "QR-to-Text" },
      { to: "/tools/ocr-to-text", label: "OCR-to-Text" },
      { to: "/tools/llm-to-text", label: "LLM-to-Text" },
    ],
  },
  {
    items: [{ to: "/database", label: "数据库配置" }],
  },
];

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar__brand">
          <strong>文件文本解析</strong>
          <span>管理后台</span>
        </div>
        <nav className="sidebar__nav">
          {NAV_GROUPS.map((group, gi) => (
            <div key={gi} className="nav-group">
              {group.label && <div className="nav-group__title">{group.label}</div>}
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    isActive
                      ? `nav-link${group.label ? " nav-link--sub" : ""} nav-link--active`
                      : `nav-link${group.label ? " nav-link--sub" : ""}`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<ParsePage />} />
          <Route path="/categories" element={<CategoryPage />} />
          <Route path="/templates" element={<TemplatePage />} />
          <Route path="/templates/:id/maker" element={<TemplateMakerPage />} />
          <Route path="/templates/:id/fields" element={<TemplateFieldsPage />} />
          <Route path="/path-configs" element={<PathConfigPage />} />
          <Route path="/file-jobs" element={<FileJobPage />} />
          <Route path="/flow-test" element={<FlowTestPage />} />
          <Route path="/tools/qr-to-text" element={<QrToTextPage />} />
          <Route path="/tools/ocr-to-text" element={<OcrToTextPage />} />
          <Route path="/tools/llm-to-text" element={<LlmToTextPage />} />
          <Route path="/database" element={<DatabasePage />} />
        </Routes>
      </main>
    </div>
  );
}
