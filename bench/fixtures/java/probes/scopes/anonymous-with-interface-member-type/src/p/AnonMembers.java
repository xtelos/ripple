package p;

interface Api {
    class Request { void send() {} }
    void call(Request r);
}
class Req { void send() {} }
public class AnonMembers {
    void f() {
        class Request { void send() {} }
        Api api = new Api() {
            public void call(Request r) { r.send(); }
        };
        Request local = new Request();
        local.send();
        class Impl implements Api {
            public void call(Request r) { r.send(); }
        }
    }
}
