package p;

import java.util.List;

class Base { static class List { void size() {} } }
class Sub extends Base {
    void f(List l) { l.size(); }
    class In {
        void g(List l) { l.size(); }
    }
}
class Unrelated {
    void f(java.util.List<String> l) { l.size(); }
}
public class Members {}
