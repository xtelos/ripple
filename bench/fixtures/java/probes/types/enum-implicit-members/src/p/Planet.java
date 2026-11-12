package p;

public class Planet {
    enum Kind implements Runnable {
        ROCK, GAS;
        public void run() {}
        static Kind parse(String s) { return valueOf(s); }
        int rank() { return ordinal(); }
    }
    void use(Kind k) {
        Kind.valueOf("ROCK");
        Kind.values();
        k.name();
        k.ordinal();
        k.compareTo(Kind.GAS);
        k.run();
        k.rank();
        Kind.parse("GAS");
        k.toString();
        k.equals(Kind.ROCK);
    }
}
