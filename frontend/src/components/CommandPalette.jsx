// Phase 8: canonical import path for the command palette.
// Single implementation lives in AppShell/CommandPalette (mounted by AppShell,
// which App.jsx renders for every terminal route). This shim keeps
// `components/CommandPalette` imports working without duplicating the dialog.
export { CommandPalette, CommandPalette as default } from "./AppShell/CommandPalette";
