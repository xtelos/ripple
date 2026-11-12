package p;

import java.util.ArrayList;
import java.util.List;

class MyList extends ArrayList<String> {
    @Override
    public boolean add(String s) {
        return super.add(s);
    }
}

class UseList {
    void f() {
        List<String> l = new MyList();
        l.add("x");
    }
}
