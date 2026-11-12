package p;

public class Res {
    static class Conn implements AutoCloseable {
        void send() {}
        public void close() {}
    }
    static class Failure extends RuntimeException {
        void report() {}
    }
    void use() {
        try (Conn c = new Conn(); Conn d = new Conn()) {
            c.send();
            d.send();
        } catch (Failure f) {
            f.report();
        } catch (IllegalStateException | IllegalArgumentException e) {
            e.getMessage();
        } finally {
            new Conn().send();
        }
    }
}
