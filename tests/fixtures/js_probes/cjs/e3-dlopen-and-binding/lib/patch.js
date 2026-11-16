try { process.dlopen({}, '/nonexistent.node'); } catch (e) {}
try { process.binding('natives'); } catch (e) {}
