package p;

class Event {}
class Click extends Event {}
abstract class Handler<E> {
    abstract void handle(E e);
    void handle(E e, int priority) {}
}
class ClickHandler extends Handler<Click> {
    void handle(Click c) {}
    void handle(Event e) {}
    void handle(Click c, int p) {}
}
class AnyHandler extends Handler<Event> {
    void handle(Event e) {}
}

public class Handlers {
    void h(Handler<Click> hc, Handler<Event> he) {
        hc.handle(new Click());
        hc.handle(new Click(), 1);
        he.handle(new Event());
        new ClickHandler().handle(new Event());
    }
}
