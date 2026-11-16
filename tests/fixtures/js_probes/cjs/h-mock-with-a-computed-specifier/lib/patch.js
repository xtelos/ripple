const jest = { mock() {} };
jest.mock(require.resolve('./a'), () => ({ helper: () => 'mocked' }));
