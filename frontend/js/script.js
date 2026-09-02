function registerDevice() {
    window.location.href = "register.html";
}


function trackDevice() {
    window.location.href = "track.html";
}


async function searchDevice() {
    const deviceId = document.getElementById("deviceId").value.trim();

    if (deviceId === "") {
        alert("Please enter a Device ID.");
        return;
    }

    try {
        const response = await fetch(
            `http://127.0.0.1:5000/devices/${deviceId}`
        );

        const result = await response.json();

        if (result.status === "success") {
            alert(
                "Device Found!\n\n" +
                "Device ID: " + result.data.device_id + "\n" +
                "Brand: " + result.data.brand + "\n" +
                "Model: " + result.data.model + "\n" +
                "Serial Number: " + result.data.serial_number + "\n" +
                "Status: " + result.data.status
            );
        } else {
            alert("Device not found.");
        }

    } catch (error) {
        console.error(error);
        alert("Unable to connect to the backend.");
    }
}
const transferForm = document.getElementById("transferForm");

if (transferForm) {
    transferForm.addEventListener("submit", async function(event) {
        event.preventDefault();

        const deviceId =
            document.getElementById("transferDeviceId").value.trim();

        const fromUser =
            document.getElementById("fromUser").value.trim();

        const toUser =
            document.getElementById("toUser").value.trim();

        if (!deviceId || !fromUser || !toUser) {
            alert("Please complete all required fields.");
            return;
        }

        try {
            const response = await fetch(
                `http://127.0.0.1:5000/devices/${deviceId}/transfer`,
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        from_user: Number(fromUser),
                        to_user: Number(toUser)
                    })
                }
            );

            const result = await response.json();

            if (result.status === "success") {
                alert(
                    "Ownership transferred successfully!\\n\\n" +
                    "Device ID: " + result.data.device_id + "\\n" +
                    "From User: " + result.data.from_user + "\\n" +
                    "To User: " + result.data.to_user + "\\n" +
                    "Transaction: " + result.data.transaction_type
                );

                transferForm.reset();

            } else {
                alert(
                    "Transfer failed:\\n" +
                    result.error
                );
            }

        } catch (error) {
            console.error(error);
            alert("Unable to connect to the backend.");
        }
    });
}
const registerForm = document.getElementById("registerForm");

if (registerForm) {
    registerForm.addEventListener("submit", async function(event) {
        event.preventDefault();

        const deviceType = document.getElementById("deviceType").value;
        const brand = document.getElementById("brand").value.trim();
        const model = document.getElementById("model").value.trim();
        const serialNumber = document.getElementById("serialNumber").value.trim();

        if (!deviceType || !brand || !model || !serialNumber) {
            alert("Please fill in all required fields.");
            return;
        }

        try {
            const response = await fetch(
                "http://127.0.0.1:5000/devices",
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        device_type: deviceType,
                        brand: brand,
                        model: model,
                        serial_number: serialNumber
                    })
                }
            );

            const result = await response.json();

            if (result.status === "success") {
                alert(
                    "Laptop registered successfully!\n\n" +
                    "Device ID: " + result.data.device_id + "\n" +
                    "Brand: " + result.data.brand + "\n" +
                    "Model: " + result.data.model + "\n" +
                    "Status: " + result.data.status
                );

                registerForm.reset();
            } else {
                alert("Registration failed: " + result.error);
            }

        } catch (error) {
            console.error(error);
            alert("Unable to connect to the backend.");
        }
    });
}