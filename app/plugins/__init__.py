from .browser_plugin import register as register_browser


class PluginRegistry:
    def __init__(self):
        self.plugins = {}

    def load_defaults(self):
        plugin = register_browser()
        self.plugins[plugin.name] = plugin

    def handlers(self):
        out = {}
        for plugin in self.plugins.values():
            out.update(plugin.handlers)
        return out

    def list(self):
        return [{"name": p.name, "description": p.description, "actions": sorted(list(p.handlers.keys()))} for p in self.plugins.values()]
