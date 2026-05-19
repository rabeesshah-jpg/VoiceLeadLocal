const NAV_LINKS = [
  { label: 'Work', href: '#' },
  { label: 'Talks', href: '#' },
  { label: 'Articles', href: '#' },
  { label: 'Books', href: '#' },
];

export default function PageHeader() {
  return (
    <header className="page-header">
      <div className="brand-label">AI Assistant</div>
      <nav className="page-nav" aria-label="Site">
        <ul className="page-nav-list">
          {NAV_LINKS.map((link, i) => (
            <li key={link.label}>
              {i > 0 && <span className="page-nav-sep" aria-hidden>/</span>}
              <a href={link.href}>{link.label}</a>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}
