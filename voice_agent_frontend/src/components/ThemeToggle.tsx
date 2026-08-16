import { IconMoon, IconSun } from './icons';
import type { ThemeMode } from '../lib/theme';

interface Props {
  theme: ThemeMode;
  onChange: (theme: ThemeMode) => void;
}

export default function ThemeToggle({ theme, onChange }: Props) {
  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Color theme">
      <button
        type="button"
        className={`theme-icon-btn ${theme === 'light' ? 'theme-icon-btn--active' : ''}`}
        aria-label="Light mode"
        aria-pressed={theme === 'light'}
        onClick={() => onChange('light')}
      >
        <IconSun />
      </button>
      <button
        type="button"
        className={`theme-icon-btn ${theme === 'dark' ? 'theme-icon-btn--active' : ''}`}
        aria-label="Dark mode"
        aria-pressed={theme === 'dark'}
        onClick={() => onChange('dark')}
      >
        <IconMoon />
      </button>
    </div>
  );
}