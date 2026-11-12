package p;

class SItem {
    void add(int x) {}
}

class SMoney {
    void add(int x) {}
}

class Sw {
    SMoney x = new SMoney();

    void f(int k) {
        switch (k) {
            case 1:
                SItem x = new SItem();
                break;
            case 2:
                x = new SItem();
                x.add(1);
                break;
            default:
                break;
        }
    }
}
