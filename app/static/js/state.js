/* ═══════════════════════════════════════════════════════════════
   STATE
   ═══════════════════════════════════════════════════════════════ */

export const state = {
  currentScreen: 'dashboard',
  wizardStep: 0,
  wizardData: { profile: {}, preferences: {}, resume_file: null },
  tagInputs: {},            // id -> string[]
  botStatus: 'stopped',     // running | paused | stopped
  botStartTime: null,
  uptimeInterval: null,
  stats: { found: 0, applied: 0, errors: 0 },
  appPage: 1,
  appPageSize: 15,
  searchTimeout: null,
  editingFile: null,        // filename when editing, null when creating
  aiAvailable: false,
  profileFiles: [],         // cached files from /api/profile/experiences
};
