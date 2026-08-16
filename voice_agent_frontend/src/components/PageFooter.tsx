import { useTheme } from '../lib/theme';
import ThemeToggle from './ThemeToggle';

export default function PageFooter() {
  const year = new Date().getFullYear();
  const { theme, setTheme } = useTheme();

  return (
    <footer className="page-footer">
      <span className="page-footer-copy">&copy; {year} | Agent Nora</span>
      <ThemeToggle theme={theme} onChange={setTheme} />
    </footer>
  );
}