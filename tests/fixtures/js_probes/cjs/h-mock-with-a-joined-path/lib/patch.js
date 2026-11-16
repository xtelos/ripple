const path = require('path');
const vi = { mock() {} };
vi.mock(path.join(__dirname, 'a'), () => ({}));
