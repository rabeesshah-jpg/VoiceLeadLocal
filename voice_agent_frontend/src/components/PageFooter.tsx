import { IconMonitor, IconMoon, IconSun } from './icons';

export default function PageFooter() {
  const year = new Date().getFullYear();

  return (
    <footer className="page-footer">
      <span className="page-footer-copy">&copy; {year} | Voice Agent</span>
      <div className="page-footer-themes" aria-label="Theme (display only)">
        <button type="button" className="theme-icon-btn theme-icon-btn--active" aria-label="Light mode">
          <IconSun />
        </button>
        <button type="button" className="theme-icon-btn" aria-label="Dark mode" disabled>
          <IconMoon />
        </button>
        <button type="button" className="theme-icon-btn" aria-label="System mode" disabled>
          <IconMonitor />
        </button>
      </div>
    </footer>
  );
}
