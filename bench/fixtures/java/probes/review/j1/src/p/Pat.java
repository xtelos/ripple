package p;

class PItem {
    void add(int x) {}
}

class PMoney {
    void add(int x) {}
}

class Pat {
    PMoney total = new PMoney();

    void f(Object o) {
        if (o instanceof PItem total) {
            total.add(1);
        }
        total.add(2);
    }
}
