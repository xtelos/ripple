'use strict';
const m = { f() { return this; } }.f;
const G = m();
