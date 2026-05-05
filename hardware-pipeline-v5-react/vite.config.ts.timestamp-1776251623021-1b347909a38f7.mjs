import "node:module";
import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import.meta.url;
var vite_config_default = defineConfig({
	plugins: [react(), viteSingleFile()],
	resolve: { alias: { "@": path.resolve("/sessions/kind-magical-lamport/mnt/AI_S2S/hardware-pipeline-v5-react", "./src") } },
	build: { target: "es2015" }
});
//#endregion
export { vite_config_default as default };

//# sourceMappingURL=data:application/json;charset=utf-8;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoidml0ZS5jb25maWcuanMiLCJuYW1lcyI6W10sInNvdXJjZXMiOlsiL3Nlc3Npb25zL2tpbmQtbWFnaWNhbC1sYW1wb3J0L21udC9BSV9TMlMvaGFyZHdhcmUtcGlwZWxpbmUtdjUtcmVhY3Qvdml0ZS5jb25maWcudHMiXSwic291cmNlc0NvbnRlbnQiOlsiaW1wb3J0IHBhdGggZnJvbSAncGF0aCc7XHJcbmltcG9ydCByZWFjdCBmcm9tICdAdml0ZWpzL3BsdWdpbi1yZWFjdCc7XHJcbmltcG9ydCB7IGRlZmluZUNvbmZpZyB9IGZyb20gJ3ZpdGUnO1xyXG5pbXBvcnQgeyB2aXRlU2luZ2xlRmlsZSB9IGZyb20gJ3ZpdGUtcGx1Z2luLXNpbmdsZWZpbGUnO1xyXG5cclxuZXhwb3J0IGRlZmF1bHQgZGVmaW5lQ29uZmlnKHtcclxuICBwbHVnaW5zOiBbcmVhY3QoKSwgdml0ZVNpbmdsZUZpbGUoKV0sXHJcbiAgcmVzb2x2ZTogeyBhbGlhczogeyAnQCc6IHBhdGgucmVzb2x2ZShfX2Rpcm5hbWUsICcuL3NyYycpIH0gfSxcclxuICBidWlsZDoge1xyXG4gICAgdGFyZ2V0OiAnZXMyMDE1JyxcclxuICB9LFxyXG59KTtcclxuIl0sIm1hcHBpbmdzIjoiOzs7Ozs7QUFLQSxJQUFBLHNCQUFlLGFBQWE7Q0FDMUIsU0FBUyxDQUFDLE9BQU8sRUFBRSxnQkFBZ0IsQ0FBQztDQUNwQyxTQUFTLEVBQUUsT0FBTyxFQUFFLEtBQUssS0FBSyxRQVBSLHdFQU8yQixRQUFRLEVBQUUsRUFBRTtDQUM3RCxPQUFPLEVBQ0wsUUFBUSxVQUNUO0NBQ0YsQ0FBQyJ9