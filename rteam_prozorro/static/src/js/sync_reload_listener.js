/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";

// Listens for the `prozorro.sync.done` bus notification (pushed by
// `prozorro.tender._notify_sync_result` when a sync run finishes) and
// triggers a full page reload so the Settings status block, the
// subscription in-progress banner, and the matched_count counter all
// pick up the fresh `prozorro.sync.cursor` state.
//
// This is the Option B fix from the design conversation: simple, but
// fires regardless of which view the user is currently on. Replace
// with a view-conditional Option C (OWL action.doAction with
// soft_reload only when the visible model is prozorro.subscription /
// res.config.settings) before the apps.odoo.com submission polish.
export const prozorroSyncReloadService = {
    dependencies: ["bus_service"],
    start(env, { bus_service }) {
        bus_service.subscribe("prozorro.sync.done", () => {
            browser.location.reload();
        });
        bus_service.start();
    },
};

registry
    .category("services")
    .add("rteam_prozorro.sync_reload", prozorroSyncReloadService);
