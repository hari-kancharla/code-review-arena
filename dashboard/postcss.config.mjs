// Tailwind was removed: the dashboard styles entirely with its own CSS in
// globals.css and used no Tailwind utility classes, so the framework only ever
// contributed its preflight reset. Autoprefixer stays for vendor prefixes.
const config = { plugins: { autoprefixer: {} } };
export default config;
